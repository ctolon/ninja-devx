# Test-only S3 server, built from the upstream Go module with checksum verification.
FROM golang@sha256:e0174e51e81218523251d85d248a90d24c3d5e81543b4f07a5d66229397db190 AS build
ENV CGO_ENABLED=0 GOPROXY=https://proxy.golang.org GOSUMDB=sum.golang.org
RUN GOBIN=/out go install github.com/minio/minio@RELEASE.2025-04-22T22-12-26Z
FROM python@sha256:e06cc1111ed84189e91866447f562b89faadbfbbb9937cd67e6bf4172cdb45df
COPY --from=build /out/minio /usr/local/bin/minio
USER 65532:65532
EXPOSE 9000
ENTRYPOINT ["minio"]
CMD ["server", "/tmp/data", "--address", ":9000"]
