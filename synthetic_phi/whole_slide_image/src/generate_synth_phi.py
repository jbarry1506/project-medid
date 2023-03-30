import argparse
import os
from os import listdir
from os.path import isfile, join

from isyntax import deident_isyntax_file
from svs import deident_svs_file
import uuid

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='WSI Slide Deidentifier')

    # general args
    parser.add_argument('--identified_slides_path', type=str, default='ident')
    parser.add_argument('--deidentified_slides_path', type=str, default='deident')
    parser.add_argument('--identified_metadata_path', type=str, default=None,
                        help='Store identified metadata, if specified')
    parser.add_argument('--label_image_path', type=str, default=None, help='Store label images, if specified.')
    parser.add_argument('--macro_image_path', type=str, default=None, help='Store macro images, if specified.')
    parser.add_argument('--rename_to_uuid', type=int, default=0, help='Set to 1 to rename files to a generated uuid')
    # parser.add_argument('--hash_before', type=int, default=1, help='Compute the hash of file before deidentification')  # Cheap to compute, part of copy.
    parser.add_argument('--hash_after', type=int, default=1, help='Compute the hash of file after deidentification')

    args = parser.parse_args()

    slide_map = dict()
    onlyfiles = [f for f in listdir(args.identified_slides_path) if isfile(join(args.identified_slides_path, f))]

    for file in onlyfiles:
        filename, file_extension = os.path.splitext(file)
        ident_file_path = os.path.join(args.identified_slides_path, file)
        out_file = file if args.rename_to_uuid == 0 else str(uuid.uuid1()) + file_extension
        deident_file_path = os.path.join(args.deidentified_slides_path, 'deident_' + out_file)

        if file_extension == '.isyntax':
            # TODO: save deidentification metadata (label, macro, uuid filename mapping)
            if deident_isyntax_file(ident_file_path, deident_file_path):
                print('iSyntax ' + ident_file_path + ' -> deident -> ' + deident_file_path)
            else:
                print('iSyntax failed deident: ' + ident_file_path)

        elif file_extension == '.svs':
            if deident_svs_file(ident_file_path, deident_file_path, args):
                print('SVS ' + ident_file_path + ' -> deident -> ' + deident_file_path)
            else:
                print('SVS failed deident: ' + ident_file_path)
