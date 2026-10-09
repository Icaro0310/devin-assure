FROM gcr.io/oss-fuzz-base/base-builder-python:v1
COPY . $SRC/devin-assure
WORKDIR devin-assure
COPY .clusterfuzzlite/build.sh $SRC/build.sh
