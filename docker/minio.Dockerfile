# Test-only S3 server, built from the upstream Go module with checksum verification.
FROM golang@sha256:f44f6e88636cfb311f9ebace870ded69d943f227bb3cb27d32ffd84ea18c43ea AS build
ENV CGO_ENABLED=0 GOPROXY=https://proxy.golang.org GOSUMDB=sum.golang.org
RUN GOBIN=/out go install github.com/minio/minio@RELEASE.2025-04-22T22-12-26Z
FROM python@sha256:ed86c82274b3c69b52fb5820f358f0bd7df0b603332063cb5c6e32bd220c3e6e
COPY --from=build /out/minio /usr/local/bin/minio
USER 65532:65532
EXPOSE 9000
ENTRYPOINT ["minio"]
CMD ["server", "/tmp/data", "--address", ":9000"]
