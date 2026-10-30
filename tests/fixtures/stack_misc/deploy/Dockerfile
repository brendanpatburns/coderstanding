FROM node:22-slim AS build
RUN echo hi
FROM build AS test
FROM --platform=linux/amd64 registry.local:5000/base/runtime
