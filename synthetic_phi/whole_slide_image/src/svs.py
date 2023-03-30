'''
Original function taken from: https://github.com/pearcetm/svs-deidentifier with MIT License
'''
import os
import shutil
import struct
import traceback
import uuid
import json
import hashlib

import tifffile
from PIL import Image
import numpy as np
from pylibdmtx.pylibdmtx import decode

# Read/modify TIFF files (as in the SVS files) using tiffparser library (stripped down tifffile lib)

TIFF_IMAGE_DESCRIPTION_TAG_CODE = 270

DESCRIPTION_KEY_WHITELIST = [
    # Datetime
    'Date',
    'Time',
    'Time Zone',

    # Pathology properties
    'AppMag',
    'MPP',
    'Left',
    'Top',
    'Rack',
    'Slide',

    # Image properties
    'Exposure Scale',
    'Exposure Time',
    'Filtered',
    'Focus Offset',
    'Gamma',
    'StripeWidth',

    # Scanner properties
    'ScannerType',
    # 'ScanScope ID',  # Intentionally removed.
    'SessionMode',
    'CalibrationAverageBlue',
    'CalibrationAverageGreen',
    'CalibrationAverageRed',
    'Scan Warning',
]

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


def filter_description_whitelist(description):
    desc_kv_pairs = description.split('|')
    # Do not filter the first key-value, as it is containing the scanner and image information, and thus does not
    # have a proper key.
    filtered_desc_kv_pairs = [desc_kv_pairs[0]]
    for i in range(1, len(desc_kv_pairs)):
        entries = desc_kv_pairs[i].split('=')
        assert len(entries) == 2  # Not expecting anything of the format ...|X=A=B|...
        if entries[0].strip() in DESCRIPTION_KEY_WHITELIST:
            filtered_desc_kv_pairs.append(f'{entries[0]}={entries[1]}')
        else:
            # print(f'Dropping description {entries=}')
            pass

    filtered_description = '|'.join(filtered_desc_kv_pairs)
    return filtered_description


def filter_image_description_tag_whitelist(slide_path):
    identified_tags = {}  # Using map in case we would later prefer some other key, e.g. resolutions.
    with open(slide_path, 'r+b') as fp:
        t = tifffile.TiffFile(fp)
        for page_index, page in enumerate(t.pages):
            for key, tag in page.tags.items():
                if key == TIFF_IMAGE_DESCRIPTION_TAG_CODE:
                    # Preserve the original description in case needed later on.
                    identified_tags[page_index] = tag.value
                    filtered_description = filter_description_whitelist(tag.value)
                    tag.overwrite(filtered_description)

    return identified_tags


def copy_with_hash(source_file, dest_file):
    BUF_SIZE = 1024 * 1024
    sha1 = hashlib.sha1()
    with open(source_file, 'rb') as f_src:
        with open(dest_file, 'wb') as f_dst:
            while data := f_src.read(BUF_SIZE):
                sha1.update(data)
                f_dst.write(data)
    return sha1.hexdigest()


def compute_hash(source_file):
    BUF_SIZE = 1024 * 1024
    sha1 = hashlib.sha1()
    with open(source_file, 'rb') as f_src:
        while data := f_src.read(BUF_SIZE):
            sha1.update(data)
    return sha1.hexdigest()


def save_label_macro_image(filename_prefix, target_path_or_none, image, original_file_path):
    if target_path_or_none is not None:
        image_filename = os.path.basename(original_file_path)
        Image.fromarray(image).save(f'{target_path_or_none}/{filename_prefix}{image_filename}.png')


def deident_svs_file(original_file_path, deident_file_path, args):
    try:
        dst_path = os.path.dirname(deident_file_path)
        tmp_file = os.path.join(dst_path, str(uuid.uuid1()))
        # create a tmp file to strip information
        hash_sha1_before = copy_with_hash(original_file_path, tmp_file)

        identified_tags = filter_image_description_tag_whitelist(tmp_file)

        # Keep label image due to a bug in QuPath/bioformats (https://github.com/ome/bioformats/pull/3962). The contents
        # (pixels) are removed, only the image record remains, and appears as black image in QuPath. The macro image
        # below, if kept, results in a stack trace in QuPath. This seems to be due to jpeg vs lzw compression used.
        label_image = delete_associated_image(tmp_file, 'label', keep_image_entry=True)
        save_label_macro_image('label_', args.label_image_path, label_image, original_file_path)

        macro_image = delete_associated_image(tmp_file, 'macro', keep_image_entry=False)
        save_label_macro_image('macro_', args.macro_image_path, macro_image, original_file_path)

        shutil.move(tmp_file, deident_file_path)

        if args.hash_after:
            hash_sha1_after = compute_hash(deident_file_path)
        else:
            hash_sha1_after = ''

        if args.identified_metadata_path is not None:
            metadata_filename = os.path.basename(original_file_path)
            result = decode(label_image)
            metadata = {
                'deident_filename': os.path.basename(deident_file_path),
                'barcode': '' if len(result) == 0 else result[0].data.decode("utf-8"),
                'hash_sha1_before': hash_sha1_before,
                'hash_sha1_after': hash_sha1_after,
                'tags': identified_tags,
            }
            with open(f'{args.identified_metadata_path}/{metadata_filename}.json', "wt") as f:
                f.write(json.dumps(metadata, indent=2))

        return True
    except:
        traceback.print_exc()
        return False
