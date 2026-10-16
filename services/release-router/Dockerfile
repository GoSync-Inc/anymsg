FROM golang:1.26.2-bookworm@sha256:47ce5636e9936b2c5cbf708925578ef386b4f8872aec74a67bd13a627d242b19 AS build
WORKDIR /src
COPY services/release-router/go.mod ./
COPY services/release-router/*.go ./
RUN CGO_ENABLED=0 go build -trimpath -buildvcs=false -ldflags="-s -w" -o /deedkit-release-router .

FROM scratch
ARG VERSION_REF
LABEL org.opencontainers.image.revision=$VERSION_REF \
      org.opencontainers.image.source="https://github.com/GoSync-Inc/deedkit"
COPY --from=build /deedkit-release-router /deedkit-release-router
USER 0:0
ENTRYPOINT ["/deedkit-release-router"]
CMD ["--config", "/run/deedkit-router/router.json"]
