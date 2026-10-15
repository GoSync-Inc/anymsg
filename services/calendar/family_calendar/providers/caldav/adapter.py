import hashlib
import re
from urllib.parse import urljoin
from xml.etree.ElementTree import Element, SubElement, tostring

from defusedxml.ElementTree import fromstring

from family_calendar.errors import CalendarError
from .transport import provider_url, within_collection

DAV = "DAV:"
CAL = "urn:ietf:params:xml:ns:caldav"


def _xml_properties(body):
    try:
        root = fromstring(body)
        if root.tag != f"{{{DAV}}}multistatus":
            raise ValueError
        results = []
        for response in root.findall(f"{{{DAV}}}response"):
            href = response.findtext(f"{{{DAV}}}href")
            props = {}
            for propstat in response.findall(f"{{{DAV}}}propstat"):
                status = propstat.findtext(f"{{{DAV}}}status", "")
                if re.search(r"\s200\s", status):
                    prop = propstat.find(f"{{{DAV}}}prop")
                    if prop is not None:
                        props.update({child.tag: child for child in prop})
            if href is not None:
                results.append((href, props))
        return results
    except Exception:
        raise CalendarError("invalid_provider_xml") from None


def _propfind(names):
    root = Element(f"{{{DAV}}}propfind")
    prop = SubElement(root, f"{{{DAV}}}prop")
    for namespace, name in names:
        SubElement(prop, f"{{{namespace}}}{name}")
    return tostring(root, encoding="utf-8", xml_declaration=True)


def _strong_etag(etag):
    if not etag or not re.fullmatch(r'"[^"\r\n]+"', etag):
        raise CalendarError("conditional_write_unavailable")
    return etag


class CalDAVAdapter:
    def __init__(self, connection_id, transport):
        self.connection_id, self.transport = connection_id, transport

    async def _props(self, url, names, depth="0"):
        r = await self.transport.request(
            "PROPFIND",
            url,
            content=_propfind(names),
            headers={"Depth": depth, "Content-Type": "application/xml; charset=utf-8"},
        )
        return str(r.request.url), _xml_properties(r.content)

    async def discover(self):
        base, principal_props = await self._props(
            "https://caldav.icloud.com/", [(DAV, "current-user-principal")]
        )
        href = next(
            (
                p[f"{{{DAV}}}current-user-principal"].findtext(f"{{{DAV}}}href")
                for _, p in principal_props
                if f"{{{DAV}}}current-user-principal" in p
            ),
            None,
        )
        if not href:
            raise CalendarError("principal_discovery_failed")
        principal = provider_url(urljoin(base, href))
        base, home_props = await self._props(principal, [(CAL, "calendar-home-set")])
        homes = [
            element.text
            for _, p in home_props
            if f"{{{CAL}}}calendar-home-set" in p
            for element in p[f"{{{CAL}}}calendar-home-set"].findall(f"{{{DAV}}}href")
        ]
        if not homes:
            raise CalendarError("calendar_home_discovery_failed")
        calendars = []
        for home in homes:
            base, values = await self._props(
                provider_url(urljoin(base, home)),
                [
                    (DAV, "resourcetype"),
                    (DAV, "displayname"),
                    (DAV, "getetag"),
                    (DAV, "current-user-privilege-set"),
                    (CAL, "calendar-timezone"),
                ],
                depth="1",
            )
            for href, props in values:
                resource_type = props.get(f"{{{DAV}}}resourcetype")
                if resource_type is None or resource_type.find(f"{{{CAL}}}calendar") is None:
                    continue
                full = provider_url(urljoin(base, href)).rstrip("/") + "/"
                privilege = props.get(f"{{{DAV}}}current-user-privilege-set")
                access = "unknown"
                if privilege is not None:
                    writable = any(
                        privilege.find(f".//{{{DAV}}}{tag}") is not None
                        for tag in ("all", "write", "write-content")
                    )
                    access = "write" if writable else "read_only"
                label = props.get(f"{{{DAV}}}displayname")
                etag = props.get(f"{{{DAV}}}getetag")
                zone = props.get(f"{{{CAL}}}calendar-timezone")
                ref = "cal-" + hashlib.sha256((self.connection_id + "\0" + full).encode()).hexdigest()[:20]
                calendars.append(
                    {
                        "ref": ref,
                        "href": full,
                        "label": label.text if label is not None else "Calendar",
                        "privilege": access,
                        "collection_etag": etag.text if etag is not None else None,
                        "timezone_ical": zone.text if zone is not None else None,
                    }
                )
        return calendars

    async def read(self, href):
        r = await self.transport.request("GET", href, allow_missing=True)
        if r.status_code == 404:
            return None
        return {"href": href, "data": r.content, "etag": r.headers.get("etag")}

    async def put(self, href, data, *, etag=None, create=False):
        headers = {"Content-Type": "text/calendar; charset=utf-8"}
        headers["If-None-Match" if create else "If-Match"] = "*" if create else _strong_etag(etag)
        return await self.transport.request("PUT", href, content=data, headers=headers)

    async def delete(self, href, etag):
        return await self.transport.request(
            "DELETE", href, headers={"If-Match": _strong_etag(etag)}, allow_missing=True
        )

    async def read_window(self, collection, start, end):
        root = Element(f"{{{CAL}}}calendar-query")
        prop = SubElement(root, f"{{{DAV}}}prop")
        for tag in (f"{{{DAV}}}getetag", f"{{{CAL}}}calendar-data"):
            SubElement(prop, tag)
        filters = SubElement(root, f"{{{CAL}}}filter")
        calendar = SubElement(filters, f"{{{CAL}}}comp-filter", {"name": "VCALENDAR"})
        event = SubElement(calendar, f"{{{CAL}}}comp-filter", {"name": "VEVENT"})
        SubElement(event, f"{{{CAL}}}time-range", {"start": start, "end": end})
        r = await self.transport.request(
            "REPORT",
            collection,
            content=tostring(root),
            headers={"Depth": "1", "Content-Type": "application/xml"},
        )
        resources = []
        for href, props in _xml_properties(r.content):
            full = provider_url(urljoin(collection, href))
            if not within_collection(full, collection):
                raise CalendarError("provider_resource_outside_calendar")
            data = props.get(f"{{{CAL}}}calendar-data")
            etag = props.get(f"{{{DAV}}}getetag")
            if data is None or data.text is None:
                raise CalendarError("coverage_incomplete")
            resources.append(
                {"href": full, "data": data.text.encode(), "etag": etag.text if etag is not None else None}
            )
        return {"resources": resources, "coverage": "complete_window", "series_inventory_complete": False}
