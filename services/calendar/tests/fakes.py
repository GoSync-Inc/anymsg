import httpx


class MemoryDAV:
    def __init__(self):
        self.resources = {}
        self.puts = 0
        self.version = 0

    def __call__(self, req):
        key = str(req.url)
        current = self.resources.get(key)
        if req.method == "GET":
            return (
                httpx.Response(200, content=current[0], headers={"etag": current[1]})
                if current
                else httpx.Response(404)
            )
        if req.method == "PUT":
            if req.headers.get("if-none-match") == "*" and current:
                return httpx.Response(412)
            if req.headers.get("if-match") and (current is None or current[1] != req.headers["if-match"]):
                return httpx.Response(412)
            self.version += 1
            self.puts += 1
            etag = f'"v{self.version}"'
            self.resources[key] = (req.content, etag)
            return httpx.Response(201, headers={"etag": etag})
        if req.method == "DELETE":
            if current and req.headers.get("if-match") != current[1]:
                return httpx.Response(412)
            self.resources.pop(key, None)
            return httpx.Response(204)
        return httpx.Response(405)
