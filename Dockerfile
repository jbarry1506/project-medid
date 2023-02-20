# docker build -t project-medid .
# docker run -it --rm -v /mnt/scratch/test_uofl_deident/slides_with_labels/:/mnt/identified -v /mnt/scratch/test_uofl_deident/slides_deidentified/:/mnt/deidentified -v /mnt/scratch/test_uofl_deident/metadata_identified:/mnt/metadata project-medid /bin/bash
# python3 synthetic_phi/whole_slide_image/src/generate_synth_phi.py --identified_slides_path /mnt/identified/ --deidentified_slides_path /mnt/deidentified/ --identified_metadata_path /mnt/metadata/

FROM python:3.8-slim

LABEL maintainer="alexandr.virodov@uky.edu"
LABEL version="0.1"
LABEL description="Docker image for project-medid deidentification tools"

RUN apt-get update && apt-get install -y \
  dmtx-utils \
  && rm -rf /var/lib/apt/lists/*

ADD . /opt/project-medid
WORKDIR /opt/project-medid

RUN pip3 install -r requirements.txt
