# docker build -t project-medid .
# docker run -it --rm -v c:\projects\pathology\deidentification\test_identified:/mnt/identified -v c:\projects\pathology\deidentification\test_deidentified:/mnt/deidentified project-medid /bin/bash
# python3 synthetic_phi/whole_slide_image/src/generate_synth_phi.py --identified_slides_path /mnt/identified/ --deidentified_slides_path /mnt/deidentified/

FROM python:3.8-slim

LABEL maintainer="alexandr.virodov@uky.edu"
LABEL version="0.1"
LABEL description="Docker image for project-medid deidentification tools"

ADD . /opt/project-medid
WORKDIR /opt/project-medid

RUN pip3 install -r requirements.txt