'''
Original function taken from: https://github.com/pearcetm/svs-deidentifier with MIT License
'''
import os
import shutil
import struct
import traceback
import uuid
import json

import tifffile
from PIL import Image
import numpy as np
from pylibdmtx.pylibdmtx import decode

# Read/modify TIFF files (as in the SVS files) using tiffparser library (stripped down tifffile lib)

def write_bytes_with_debug(fp, some_bytes, name):
    # print(f'{fp.tell()=} {len(some_bytes)=} {name=} {some_bytes[:8]=}')
    fp.write(some_bytes)



# delete_associated_image will remove a label or macro image from an SVS file
def delete_associated_image(slide_path, image_type, keep_image_entry):
    # THIS WILL ONLY WORK FOR STRIPED IMAGES CURRENTLY, NOT TILED

    allowed_image_types = ['label', 'macro'];
    if image_type not in allowed_image_types:
        raise Exception('Invalid image type requested for deletion')

    with open(slide_path, 'r+b') as fp:
        t = tifffile.TiffFile(fp)

        # logic here will depend on file type. AT2 and older SVS files have "label" and "macro"
        # strings in the page descriptions, which identifies the relevant pages to modify.
        # in contrast, the GT450 scanner creates svs files which do not have this, but the label
        # and macro images are always the last two pages and are striped, not tiled.
        # The header of the first page will contain a description that indicates which file type it is
        first_page = t.pages[0]
        filtered_pages = []
        if 'Aperio Image Library' in first_page.description:
            filtered_pages = [page for page in t.pages if image_type in page.description]
        elif 'Aperio Leica Biosystems GT450' in first_page.description:
            if image_type == 'label':
                filtered_pages = [t.pages[-2]]
            else:
                filtered_pages = [t.pages[-1]]
        else:
            # default to old-style labeled pages
            filtered_pages = [page for page in t.pages if image_type in page.description]

        num_results = len(filtered_pages)
        if num_results > 1:
            raise Exception(f'Invalid SVS format: duplicate associated {image_type} images found')
        if num_results == 0:
            # No image of this type in the WSI file; no need to delete it
            return None

        # At this point, exactly 1 image has been identified to remove
        page = filtered_pages[0]
        image = page.asarray()

        # get the list of IFDs for the various pages
        offsetformat = t.tiff.offsetformat
        offsetsize = t.tiff.offsetsize
        tagnoformat = t.tiff.tagnoformat
        tagnosize = t.tiff.tagnosize
        tagsize = t.tiff.tagsize
        unpack = struct.unpack

        # start by saving this page's IFD offset
        ifds = [{'this': p.offset} for p in t.pages]
        # now add the next page's location and offset to that pointer
        for p in ifds:
            # move to the start of this page
            fp.seek(p['this'])
            # read the number of tags in this page
            (num_tags,) = unpack(tagnoformat, fp.read(tagnosize))

            # move forward past the tag defintions
            fp.seek(num_tags * tagsize, 1)
            # add the current location as the offset to the IFD of the next page
            p['next_ifd_offset'] = fp.tell()
            # read and save the value of the offset to the next page
            (p['next_ifd_value'],) = unpack(offsetformat, fp.read(offsetsize))

        # filter out the entry corresponding to the desired page to remove
        pageifd = [i for i in ifds if i['this'] == page.offset][0]
        # find the page pointing to this one in the IFD list
        previfd = [i for i in ifds if i['next_ifd_value'] == page.offset]
        # check for errors
        if len(previfd) == 0:
            raise Exception('No page points to this one')
            return
        else:
            previfd = previfd[0]

        # get the strip offsets and byte counts
        offsets = page.tags['StripOffsets'].value
        bytecounts = page.tags['StripByteCounts'].value

        # iterate over the strips and erase the data
        # print('Deleting pixel data from image strips')
        for (o, b) in zip(offsets, bytecounts):
            fp.seek(o)
            write_bytes_with_debug(fp, b'\0' * b, 'data')

        if not keep_image_entry:
            # iterate over all tags and erase values if necessary
            # print('Deleting tag values')
            for key, tag in page.tags.items():
                fp.seek(tag.valueoffset)
                write_bytes_with_debug(fp, b'\0' * tag.count, f'tag {key=}')  # TODO: should be valuebytecount?

            offsetsize = t.tiff.offsetsize
            offsetformat = t.tiff.offsetformat
            pagebytes = (pageifd['next_ifd_offset'] - pageifd['this']) + offsetsize

            # next, zero out the data in this page's header
            # print('Deleting page header')
            fp.seek(pageifd['this'])
            write_bytes_with_debug(fp, b'\0' * pagebytes, 'header')

            # finally, point the previous page's IFD to this one's IFD instead
            # this will make it not show up the next time the file is opened
            fp.seek(previfd['next_ifd_offset'])
            write_bytes_with_debug(fp, struct.pack(offsetformat, pageifd['next_ifd_value']), 'next_ifd_value')

        return image


def deident_svs_file(original_file_path, deident_file_path, identified_metadata_path=None, label_image_path=None):
    try:
        dst_path = os.path.dirname(deident_file_path)
        tmp_file = os.path.join(dst_path, str(uuid.uuid1()))
        # create a tmp file to strip information
        shutil.copyfile(original_file_path, tmp_file)

        # Keep label image due to a bug in QuPath/bioformats (https://github.com/ome/bioformats/pull/3962). The contents
        # (pixels) are removed, only the image record remains, and appears as black image in QuPath. The macro image
        # below, if kept, results in a stack trace in QuPath. This seems to be due to jpeg vs lzw compression used.
        label_image = delete_associated_image(tmp_file, 'label', keep_image_entry=True)

        if label_image_path is not None:
            label_image_filename = os.path.basename(original_file_path)
            Image.fromarray(label_image).save(f'{label_image_path}/{label_image_filename}.png')

        if identified_metadata_path is not None:
            metadata_filename = os.path.basename(original_file_path)
            result = decode(label_image)
            metadata = {
                'deident_filename': os.path.basename(deident_file_path),
                'barcode': '' if len(result) == 0 else result[0].data.decode("utf-8")

            }
            with open(f'{identified_metadata_path}/{metadata_filename}.json', "wt") as f:
                f.write(json.dumps(metadata, indent=2))

        delete_associated_image(tmp_file, 'macro', keep_image_entry=False)

        shutil.move(tmp_file, deident_file_path)
        return True
    except:
        traceback.print_exc()
        return False
