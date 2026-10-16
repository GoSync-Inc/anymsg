FROM golang:1.26.9-bookworm@sha256:d9c68c2c51161e12fd77e4c6320687c9cd86e1af1e3ad6e6cd63ff970641453c AS build
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
