import argparse
import sys
import os
import errno
import math
from multiprocessing import Process, Queue
import time
import traceback

import logging
log = logging.getLogger(__name__)

import numpy as np
# import matplotlib
# matplotlib.use('Agg')
# import matplotlib.pyplot as plt
# import matplotlib.gridspec as gridspec
# from matplotlib.backends.backend_agg import FigureCanvasAgg as FigureCanvas

import hicmatrix.HiCMatrix as hm
from hicexplorer import utilities
from hicexplorer._version import __version__
from .lib import Viewpoint

import h5py
import io
import tarfile
from contextlib import closing
import pyBigWig
from collections import OrderedDict

from tempfile import mkdtemp
import shutil


def parse_arguments(args=None):
    parser = argparse.ArgumentParser(add_help=False,
                                     formatter_class=argparse.RawDescriptionHelpFormatter,
                                     description="""
chicExportData exports the data stored in the intermediate hdf5 files to text files per reference point.

"""
                                     )
    parserRequired = parser.add_argument_group('Required arguments')

    parserRequired.add_argument('--file', '-f',
                                help='path to the file which should be used for data export',
                                required=True)

    parserOpt = parser.add_argument_group('Optional arguments')

    parserOpt.add_argument('--outFileName', '-o',
                           help='Output tar.gz of the files. In case of --outputMode == geneName it is ignored.'
                           ' (Default: %(default)s).',
                           required=False,
                           default='data.tar.gz')

    parserOpt.add_argument('--outputFileType',
                           '-oft',
                           help='Output file type can be set for all file types to txt; except \'interaction\' supports also bigwig'
                           ' (Default: %(default)s).',
                           default='txt',
                           choices=['txt', 'bigwig', 'arcs', 'long-range-text']
                           )
    parserOpt.add_argument('--outputMode',
                           '-om',
                           help='Output mode: Either all date is written or a gene name must be specified.'
                           ' (Default: %(default)s).',
                           default='all',
                           choices=['all', 'geneName']
                           )
    parserOpt.add_argument('--outputModeName',
                           '-omn',
                           help='ONLY valid if --outputMode geneName! Define the name of the gene'
                           )
    parserOpt.add_argument('--decimalPlaces',
                           help='Decimal places for all output floating numbers in the viewpoint files'
                           ' (Default: %(default)s).',
                           type=int,
                           default=12)
    parserOpt.add_argument('--chromosomeSizes', '-cs',
                           help=('File with the chromosome sizes for your genome. A tab-delimited two column layout \"chr_name size\" is expected'
                                 'Usually the sizes can be determined from the SAM/BAM input files, however, '
                                 'for cHi-C or scHi-C it can be that at the start or end no data is present. '
                                 'Please consider that this option causes that only reads are considered which are on the listed chromosomes.'
                                 'Use this option to guarantee fixed sizes. An example file is available via UCSC: '
                                 'http://hgdownload.soe.ucsc.edu/goldenPath/dm3/bigZips/dm3.chrom.sizes'),
                           type=argparse.FileType('r'),
                           metavar='txt file')
    parserOpt.add_argument('--backgroundModelFile', '-bmf',
                           help='Path to the background model file. Required only for fileType=interactions and outputFileTypeBigwig.',
                           required=False)
    parserOpt.add_argument('--oneTargetFile', '-otf',
                           help='Compile all target files to one. Applies only if the file type is target',
                           required=False,
                           action='store_true')
    parserOpt.add_argument('--oneSignificantFile', '-osf',
                           help='Compile all significant files to one. Applies only if the file type is significant',
                           required=False,
                           action='store_true')
    parserOpt.add_argument('--oneDifferentialFile', '-odf',
                           help='Compile all rejected differential regions to one file.',
                           required=False,
                           action='store_true')
    parserOpt.add_argument('--exportOnlySignificantOrRejected', '-eo',
                           help='Export from significant or rejected files only these viewpoint which contains significant or differential regions.',
                           required=False,
                           action='store_true')
    parserOpt.add_argument('--range',
                           help='Defines the region upstream and downstream of a reference point which should be included. '
                           'Format is --range upstream downstream, e.g.: --range 500000 500000 plots 500kb up- and 500kb downstream. '
                           'This value should not exceed the range used in the other chic-tools. Applies only for interaction files in the combination with bigwig and a background model file!',
                           required=False,
                           type=int,
                           nargs=2)
    parserOpt.add_argument('--outputValueBigwigArcs',
                           '-ovb',
                           help='Select which value the bigwig / arcs file should contain: \'relative-interactions\', \'p-value\', \'x-fold\', \'raw\''
                           ' (Default: %(default)s).',
                           default='relative-interactions',
                           choices=['relative-interactions', 'p-value', 'x-fold', 'raw']
                           )
    parserOpt.add_argument('--arcsRegion', '-ar',
                           help='Only active for the outputFileType arcs. Enforce all outputs to be in the same region. WARNING: This manipulates the regions of the interactions!'
                           ' Please activate this option only for very special purposes like plotting multiple genes in one plot via pyGenomeTracks which does not support multiple different regions.'
                           'To use it, set to the desired region by defining a random reference point e.g. chr1:18000000-18001000 for a 1kb resolution' ,
                           required=False,
                           default=None,
                           type=str)
    parserOpt.add_argument('--threads', '-t',
                           help='Number of threads (uses the python multiprocessing module)'
                           ' (Default: %(default)s).',
                           required=False,
                           default=4,
                           type=int
                           )
    parserOpt.add_argument("--help", "-h", action="help", help="show this help message and exit")

    parserOpt.add_argument('--version', action='version',
                           version='%(prog)s {}'.format(__version__))
    return parser


def exportData(pFileList, pArgs, pViewpointObject, pDecimalPlace, pChromosomeSizes, pBackgroundData, pFileType, pQueue):

    file_list = []
    file_content_list = []
    chromosome_arc = None
    start_arc = None
    end_arc = None
    file_ending = '.txt' 
    if pArgs.outputFileType == 'bigwig':
        file_ending = '.bigwig' 
    elif pArgs.outputFileType == 'arcs':
        file_ending = '.arcs' 
        if pArgs.arcsRegion is not None:
            chromosome_arc, start_arc = pArgs.arcsRegion.split(':')
            start_arc, end_arc = start_arc.split('-')

    try:
        if pFileType == 'interactions' or pFileType == 'significant':
            header_information = '# Chromosome\tStart\tEnd\tGene\tSum of interactions\tRelative position\tRelative Interactions\tp-value\tx-fold\tRaw\n'

            for file in pFileList:

                if pArgs.outputFileType == 'bigwig':
                    chromosome_name = []
                    start = []
                    end = []
                    values = []
                    relative_distance = {}
                for sample in file:
                    data = pViewpointObject.readInteractionFile(pArgs.file, sample)
                    key_list = sorted(list(data[1].keys()))

                    if pArgs.outputFileType == 'txt':

                        file_content_string = header_information
                        for key in key_list:
                            file_content_string += '\t'.join('{:.{decimal_places}f}'.format(x, decimal_places=pDecimalPlace) if isinstance(x, np.float) else str(x) for x in data[1][key]) + '\n'
                    elif pArgs.outputFileType == 'arcs':
                        file_content_string = ''
                        for key in key_list:
                            # log.debug('key {}'.format(key))
                            # log.debug('data[1] {}'.format(str(data[1][key])))
                            # log.debug('data[2] {}'.format(str(data[2])))
                            
                            if pArgs.arcsRegion is not None:
                                file_content_string += '\t'.join([str(chromosome_arc), str(start_arc), str(end_arc)])
                                file_content_string += '\t'
                                file_content_string += '\t'.join([str(chromosome_arc), str(int(start_arc) + int(data[1][key][5])), str(int(end_arc) + int(data[1][key][5]))])
                                file_content_string += '\t'
                            else:
                                file_content_string += '\t'.join([str(data[1][key][0]), str(int(data[2][0])), str(int(data[2][1]))])
                                file_content_string += '\t'
                                file_content_string += '\t'.join([str(data[1][key][0]), str(int(data[1][key][1])), str(int(data[1][key][2]))])
                                file_content_string += '\t'

                            if pArgs.outputValueBigwigArcs == 'relative-interactions':
                                file_content_string += '{:.{decimal_places}f}'.format(data[1][key][6], decimal_places=pDecimalPlace)
                            elif pArgs.outputValueBigwigArcs == 'p-value':
                                file_content_string += '{:.{decimal_places}f}'.format(data[1][key][7], decimal_places=pDecimalPlace)

                            elif pArgs.outputValueBigwigArcs == 'x-fold':
                                file_content_string += '{:.{decimal_places}f}'.format(data[1][key][8], decimal_places=pDecimalPlace)

                            elif pArgs.outputValueBigwigArcs == 'raw':
                                file_content_string += '{:.{decimal_places}f}'.format(data[1][key][9], decimal_places=pDecimalPlace)
                            
                            file_content_string += '\n'
                            # log.debug('file_content_string {}'.format(file_content_string))
                    elif pArgs.outputFileType == 'long-range-text':
                        file_content_string = ''
                        
                        for key in key_list:
                            file_content_string += '\t'.join([str(data[1][key][0]), str(int(data[2][0])), str(int(data[2][1]))])
                            file_content_string += '\t'
                            file_content_string += str(data[1][key][0]) + ':' + str(int(data[1][key][1])) + '-' +  str(int(data[1][key][2]))  + ',' + str(int(data[1][key][9]))
                            file_content_string += '\n'
                            
                    else:
                        for key in key_list:
                            chromosome_name.append(str(data[1][key][0]))
                            start.append(int(data[1][key][1]))
                            end.append(int(data[1][key][2]))
                            if pArgs.outputValueBigwigArcs == 'relative-interactions':
                                values.append(float(data[1][key][6]))
                            elif pArgs.outputValueBigwigArcs == 'p-value':
                                values.append(float(data[1][key][7]))
                            elif pArgs.outputValueBigwigArcs == 'x-fold':
                                values.append(float(data[1][key][8]))
                            elif pArgs.outputValueBigwigArcs == 'raw':
                                values.append(float(data[1][key][9]))

                            relative_distance[data[1][key][5]] = [str(data[1][key][0]), int(data[1][key][1]), int(data[1][key][2])]
                        header = [(chromosome_name[0], pChromosomeSizes[chromosome_name[0]])]

                        if pArgs.backgroundModelFile is not None:
                            key_list_background = sorted(list(pBackgroundData.keys()))
                            chromosome_name_background = []
                            start_background = []
                            end_background = []
                            value_background = []

                            for key in key_list_background:
                                if key in relative_distance:
                                    chromosome_name_background.append(relative_distance[key][0])
                                    start_background.append(relative_distance[key][1])
                                    end_background.append(relative_distance[key][2])
                                    value_background.append(float(pBackgroundData[key][0]))
                # if pArgs.exportOnlySignificantOrRejected:
                #     # contains only header and no significant regions
                #     if len(file     _content_string) == 1:
                #         continue
                if pArgs.outputFileType == 'txt' or pArgs.outputFileType == 'arcs' or pArgs.outputFileType == 'long-range-text':
                    file_content_list.append(file_content_string)
                    file_name = '_'.join(sample) + '_' + pFileType + file_ending
                else:
                    if pArgs.backgroundModelFile is not None:
                        file_content_list.append([[header, chromosome_name, start, end, values], [header, chromosome_name_background, start_background, end_background, value_background]])

                        file_name = ['_'.join(sample) + file_ending, 'background_' + '_'.join(sample) + '_' + pFileType + file_ending]

                    else:
                        file_content_list.append([[header, chromosome_name, start, end, values]])
                        file_name = ['_'.join(sample) + '_' + pFileType + file_ending]

                file_list.append(file_name)
        elif pFileType == 'target':
            # targetList, present_genes = pViewpointObject.readTargetHDFFile(pArgs.file)
            # header_information = '# Chromosome\tStart\tEnd\n'

            for targetFile in pFileList:
                targetFileHDF5Object = h5py.File(pArgs.file, 'r')
                target_object = targetFileHDF5Object['/'.join(targetFile)]
                chromosome = target_object.get('chromosome')[()]
                start_list = list(target_object['start_list'][:])
                end_list = list(target_object['end_list'][:])
                targetFileHDF5Object.close()
                chromosome = [chromosome] * len(start_list)

                target_regions = list(zip(chromosome, start_list, end_list))
                file_content_string = ''
                # key_list = sorted(list(data[1].keys()))
                for region in target_regions:
                    file_content_string += '\t'.join(x.decode('utf-8') for x in region) + '\n'
                file_content_list.append(file_content_string)
                file_name = '_'.join(targetFile) + '_' + pFileType + '.txt'
                file_list.append(file_name)

        elif pFileType == 'aggregate':
            header_information = '# Chromosome\tStart\tEnd\tGene\tSum of interactions\tRelative position\tRaw\n'

            for file in pFileList:
                for sample in file:
                    line_content, data = pViewpointObject.readAggregatedFileHDF(pArgs.file, sample)
                    file_content_string = header_information
                    for line in line_content:
                        file_content_string += '\t'.join('{:.{decimal_places}f}'.format(x, decimal_places=pDecimalPlace) if isinstance(x, np.float) else str(x) for x in line) + '\n'
                    file_content_list.append(file_content_string)

                    file_name = '_'.join(sample) + '_' + pFileType + '.txt'
                    file_list.append(file_name)

        elif pFileType == 'differential':
            header_information = '# Chromosome\tStart\tEnd\tGene\tRelative distance\tsum of interactions 1\ttarget_1 raw\tsum of interactions 2\ttarget_2 raw\tp-value\n'

            for file in pFileList:
                # accepted_list, all_list, rejected_list
                item_classification = ['accepted', 'all', 'rejected']
                line_content = pViewpointObject.readDifferentialFile(pArgs.file, file)

                if pArgs.exportOnlySignificantOrRejected:
                    # log.debug('line_content {}'.format(type(line_content[2])))
                    
                    if type(line_content[2]) is not zip:
                        continue
                for i, item in enumerate(line_content):
                    # log.debug('item {}'.format(list(item)))
                    if pArgs.oneDifferentialFile:
                        # if a single file containing all rejected region should be the output, skip 'accepted' and 'all' case 
                        if item_classification[i] in ['accepted', 'all']:
                            continue
                    if pArgs.outputFileType == 'txt':
                        file_content_string = ''
                        # Add header only if not a single differential file should be created
                        if not pArgs.oneDifferentialFile:
                            file_content_string = header_information
                        

                        for line in item:
                            file_content_string += '\t'.join('{:.{decimal_places}f}'.format(x, decimal_places=pDecimalPlace) if isinstance(x, np.float) else str(x) for x in line[:10]) + '\n'
                        
                        file_content_list.append(file_content_string)
                        file_name = '_'.join(file) + '_' + item_classification[i] + '_' + pFileType + '.txt'
                        file_list.append(file_name)
                    
                    elif pArgs.outputFileType == 'long-range-text':
                        log.debug('line 339!')
                        file_content_string = ''
                        # log.debug('item {}'.format(list(item)))

                        for line in item:
                            log.debug('line 342!')

                            log.debug('line {}'.format(line))
                            file_content_string += '\t'.join([str(line[0]), str( int(line[10])) , str( int(line[11]) ) ])
                            file_content_string += '\t'
                            file_content_string += str(line[0]) + ':' + str(int(line[1])) + '-' +  str(int(line[2]))  + ',' + str(int(line[8]))
                            file_content_string += '\n'
                        log.debug('line 349!')
                        
                        file_content_list.append(file_content_string)
                        file_name = '_'.join(file) + '_' + item_classification[i] + '_' + pFileType + '.txt'
                        file_list.append(file_name)
                    elif pArgs.outputFileType == 'arcs':
                        file_content_string = ''
                        for line in item:
                            # log.debug('line {}'.format(line))
                            if pArgs.arcsRegion is not None:
                                file_content_string += '\t'.join([str(chromosome_arc), str(start_arc), str(end_arc)])
                                file_content_string += '\t'
                                file_content_string += '\t'.join([str(chromosome_arc), str(int(start_arc) + int(line[4])), str(int(end_arc) + int(line[4]))])
                                file_content_string += '\t'
                                file_content_string += '1\n'
                            else:
                                resolution = int(line[2]) - int(line[1]) 
                                file_content_string += '\t'.join([ str(line[0]), str( int(line[10])) , str( int(line[11]) ) ])
                                file_content_string += '\t'
                                file_content_string += '\t'.join([str(line[0]), str(int(line[1])), str(int(line[2]))])
                                file_content_string += '\t'
                                file_content_string += '1\n'

                          
                        file_content_list.append(file_content_string)
                        file_name = '_'.join(file) + '_' + item_classification[i] + '_' + pFileType + '.arcs'
                        file_list.append(file_name)


    except Exception as exp:
        pQueue.put('Fail: ' + str(exp) + traceback.format_exc())
        return

    pQueue.put([file_list, file_content_list])
    return


def main(args=None):
    args = parse_arguments().parse_args(args)
    viewpointObj = Viewpoint()

    fileList = []
    chromosome_sizes = None
    background_dict = None
    # read hdf file
    fileHDF5Object = h5py.File(args.file, 'r')

    fileType = fileHDF5Object.attrs['type']

    if args.outputMode == 'geneName' and args.outputModeName is None:
        log.error('Output mode is \'geneName\'. Please specify a gene name via --outputModeName too!')
        exit(1)

    if args.outputFileType == 'bigwig':
        if fileType != 'interactions':
            log.error('Only file type \'interactions\' supports bigwig. Exiting.')
            exit(1)
        if args.range is None:
            log.error('Bigwig files require the argument \'--range upstream downstream\'. Exiting.')
            exit(1)
        if args.backgroundModelFile is not None:
            if args.backgroundModelFile:
                background_dict = viewpointObj.readBackgroundDataFile(args.backgroundModelFile, args.range, args.range[1], pMean=True)
        else:
            log.error('Please define a background file via --backgroundModelFile.')
            exit(1)
        if args.chromosomeSizes is not None:
            chromosome_sizes = OrderedDict()
            with open(args.chromosomeSizes.name, 'r') as file:
                file_ = True
                while file_:
                    file_ = file.readline().strip()
                    if file_ != '':
                        line_split = file_.split('\t')
                        chromosome_sizes[line_split[0]] = int(line_split[1])
        else:
            log.error('Bigwig files require the argument \'--chromosomeSizes\'. Exiting.')
            exit(1)

    # read hdf file
    keys_file = list(fileHDF5Object.keys())
    if fileType == 'interactions' or fileType == 'significant':
        if args.outputMode == 'all':
            for i, sample in enumerate(keys_file):
                matrix_obj1 = fileHDF5Object[sample]
                chromosomeList1 = sorted(list(matrix_obj1.keys()))
                chromosomeList1.remove('genes')
                for chromosome1 in chromosomeList1:
                    geneList1 = sorted(list(matrix_obj1[chromosome1].keys()))
                    for gene1 in geneList1:
                        fileList.append([[sample, chromosome1, gene1]])
        else:
            for i, sample in enumerate(keys_file):
                matrix_obj1 = fileHDF5Object[sample]['genes']
                chromosomeList1 = sorted(list(matrix_obj1.keys()))
                gene_name = args.outputModeName
                counter = 1
                while gene_name in chromosomeList1:
                    fileList.append([[sample, 'genes', gene_name]])
                    gene_name = args.outputModeName + '_' + str(counter)
                    counter += 1
    elif fileType == 'target':
        if fileHDF5Object.attrs['combinationMode'] == 'dual':
            if args.outputMode == 'all':
                for outer_matrix in keys_file:
                    inner_matrix_object = fileHDF5Object[outer_matrix]
                    keys_inner_matrices = list(inner_matrix_object.keys())
                    for inner_matrix in keys_inner_matrices:
                        inner_object = inner_matrix_object[inner_matrix]
                        gene_object = inner_object['genes']
                        keys_genes = list(gene_object.keys())
                        for gen in keys_genes:
                            fileList.append([outer_matrix, inner_matrix, 'genes', gen])
            else:
                for outer_matrix in keys_file:
                    inner_matrix_object = fileHDF5Object[outer_matrix]
                    keys_inner_matrices = list(inner_matrix_object.keys())
                    for inner_matrix in keys_inner_matrices:
                        inner_object = inner_matrix_object[inner_matrix]['genes']
                        keys_genes = list(inner_object.keys())
                        gene_name = args.outputModeName
                        counter = 1
                        while gene_name in keys_genes:
                            fileList.append([outer_matrix, inner_matrix, 'genes', gene_name])
                            gene_name = args.outputModeName + '_' + str(counter)
                            counter += 1
        elif fileHDF5Object.attrs['combinationMode'] == 'single':

            if args.outputMode == 'all':
                for outer_matrix in keys_file:
                    gene_object = fileHDF5Object[outer_matrix]['genes']
                    keys_genes = list(gene_object.keys())
                    for gen in keys_genes:
                        fileList.append([outer_matrix, 'genes', gen])
            else:
                for outer_matrix in keys_file:
                    keys_genes = list(fileHDF5Object[outer_matrix]['genes'].keys())
                    gene_name = args.outputModeName
                    counter = 1
                    while gene_name in keys_genes:
                        fileList.append([outer_matrix, 'genes', gene_name])
                        gene_name = args.outputModeName + '_' + str(counter)
                        counter += 1

    elif fileType == 'aggregate':
        if args.outputMode == 'all':
            for i, combinationOfMatrix in enumerate(keys_file):
                keys_matrix_intern = list(fileHDF5Object[combinationOfMatrix].keys())
                if len(keys_matrix_intern) == 0:
                    continue
                matrix1 = keys_matrix_intern[0]
                matrix2 = keys_matrix_intern[1]

                matrix_obj1 = fileHDF5Object[combinationOfMatrix + '/' + matrix1]
                matrix_obj2 = fileHDF5Object[combinationOfMatrix + '/' + matrix2]

                chromosomeList1 = sorted(list(matrix_obj1.keys()))
                chromosomeList2 = sorted(list(matrix_obj2.keys()))
                chromosomeList1.remove('genes')
                chromosomeList2.remove('genes')
                for chromosome1, chromosome2 in zip(chromosomeList1, chromosomeList2):
                    geneList1 = sorted(list(matrix_obj1[chromosome1].keys()))
                    geneList2 = sorted(list(matrix_obj2[chromosome2].keys()))

                    for gene1, gene2 in zip(geneList1, geneList2):
                        fileList.append([[combinationOfMatrix, matrix1, chromosome1, gene1], [combinationOfMatrix, matrix2, chromosome2, gene2]])
        else:
            for i, combinationOfMatrix in enumerate(keys_file):
                keys_matrix_intern = list(fileHDF5Object[combinationOfMatrix].keys())
                if len(keys_matrix_intern) == 0:
                    continue
                matrix1 = keys_matrix_intern[0]
                matrix2 = keys_matrix_intern[1]

                matrix_obj1 = fileHDF5Object[combinationOfMatrix + '/' + matrix1]['genes']
                matrix_obj2 = fileHDF5Object[combinationOfMatrix + '/' + matrix2]['genes']

                chromosomeList1 = sorted(list(matrix_obj1.keys()))
                chromosomeList2 = sorted(list(matrix_obj2.keys()))
                # chromosomeList1.remove('genes')
                # chromosomeList2.remove('genes')
                gene_name = args.outputModeName
                counter = 1
                while gene_name in chromosomeList1 and gene_name in chromosomeList2:
                    fileList.append([[combinationOfMatrix, matrix1, 'genes', gene_name], [combinationOfMatrix, matrix2, 'genes', gene_name]])
                    gene_name = args.outputModeName + '_' + str(counter)
                    counter += 1

    elif fileType == 'differential':
        if args.outputMode == 'all':
            for outer_matrix in keys_file:
                inner_matrix_object = fileHDF5Object[outer_matrix]
                keys_inner_matrices = list(inner_matrix_object.keys())
                for inner_matrix in keys_inner_matrices:
                    inner_object = inner_matrix_object[inner_matrix]
                    chromosomeList = sorted(list(inner_object.keys()))
                    chromosomeList.remove('genes')
                    for chromosome in chromosomeList:
                        geneList = sorted(list(inner_object[chromosome].keys()))

                        for gene in geneList:
                            fileList.append([outer_matrix, inner_matrix, chromosome, gene])
        else:
            for outer_matrix in keys_file:
                inner_matrix_object = fileHDF5Object[outer_matrix]
                keys_inner_matrices = list(inner_matrix_object.keys())
                for inner_matrix in keys_inner_matrices:
                    inner_object = inner_matrix_object[inner_matrix]['genes']
                    chromosomeList = sorted(list(inner_object.keys()))
                    gene_name = args.outputModeName
                    counter = 1
                    while gene_name in chromosomeList:
                        fileList.append([outer_matrix, inner_matrix, 'genes', gene_name])
                        # fileList.append([outer_matrix, inner_matrix, 'genes', gene_name])
                        gene_name = args.outputModeName + '_' + str(counter)
                        counter += 1

    fileHDF5Object.close()

    filesPerThread = len(fileList) // args.threads

    all_data_collected = False
    thread_data = [None] * args.threads
    file_name_list = [None] * args.threads

    queue = [None] * args.threads
    process = [None] * args.threads
    thread_done = [False] * args.threads
    fail_flag = False
    fail_message = ''

    for i in range(args.threads):

        if i < args.threads - 1:
            fileListPerThread = fileList[i * filesPerThread:(i + 1) * filesPerThread]
        else:
            fileListPerThread = fileList[i * filesPerThread:]
        queue[i] = Queue()

        process[i] = Process(target=exportData, kwargs=dict(
            pFileList=fileListPerThread,
            pArgs=args,
            pViewpointObject=viewpointObj,
            pDecimalPlace=args.decimalPlaces,
            pChromosomeSizes=chromosome_sizes,
            pBackgroundData=background_dict,
            pFileType=fileType,
            pQueue=queue[i]
        )
        )

        process[i].start()

    while not all_data_collected:
        for i in range(args.threads):
            if queue[i] is not None and not queue[i].empty():
                return_content = queue[i].get()
                if 'Fail:' in return_content:
                    fail_flag = True
                    fail_message = return_content[6:]
                else:
                    file_name_list[i], thread_data[i] = return_content

                queue[i] = None
                process[i].join()
                process[i].terminate()
                process[i] = None
                thread_done[i] = True
        all_data_collected = True
        for thread in thread_done:
            if not thread:
                all_data_collected = False
        time.sleep(1)
    if fail_flag:
        log.error(fail_message)
        exit(1)

    thread_data = [item for sublist in thread_data for item in sublist]
    file_name_list = [item for sublist in file_name_list for item in sublist]

    if len(thread_data) == 0:
        log.error('Contains not the requested data!')
        exit(1)
    if args.outputFileType == 'txt' or args.outputFileType == 'arcs' or args.outputFileType == 'long-range-text':
        if args.outputMode == 'geneName':
            basepath = os.path.dirname(args.outFileName)
            for i, file_content_string in enumerate(thread_data):
                with open(basepath + '/' + file_name_list[i], "w") as file:
                    file.write(file_content_string)
        else:
            with tarfile.open(args.outFileName, "w:gz") as tar:

                if (args.oneTargetFile and fileType == 'target') or (args.oneSignificantFile and fileType == 'significant'):
                    if fileType == 'target':
                        file_name = 'targets.tsv'
                    elif fileType == 'significant':
                        file_name = 'significant.tsv'
                    tar_info = tarfile.TarInfo(name=file_name)
                    tar_info.mtime = time.time()
                    file_content_string_all = ''
                    for i, file_content_string in enumerate(thread_data):

                        file_content_string_all += file_content_string

                    file_content_string_all = file_content_string_all.encode('utf-8')
                    tar_info.size = len(file_content_string_all)
                    file = io.BytesIO(file_content_string_all)
                    tar.addfile(tarinfo=tar_info, fileobj=file)
                
                else:
                    for i, file_content_string in enumerate(thread_data):

                        tar_info = tarfile.TarInfo(name=file_name_list[i])
                        tar_info.mtime = time.time()
                        file_content_string = file_content_string.encode('utf-8')
                        tar_info.size = len(file_content_string)
                        file = io.BytesIO(file_content_string)
                        tar.addfile(tarinfo=tar_info, fileobj=file)

    elif args.outputFileType == 'bigwig':
        if args.outputMode == 'geneName':
            bigwig_folder = os.path.dirname(args.outFileName)
            # bigwig_folder = '.'
        else:
            bigwig_folder = mkdtemp(prefix="bigwig_folder")
        for i, file_content in enumerate(thread_data):

            for j, file_list in enumerate(file_content):

                bw = pyBigWig.open(bigwig_folder + '/' + file_name_list[i][j], 'w')
                # # set big wig header
                bw.addHeader(file_list[0])

                bw.addEntries(file_list[1], file_list[2], ends=file_list[3], values=file_list[4])
                bw.close()

        if args.outputMode == 'all':
            if not args.outFileName.endswith('.tar.gz'):
                args.outFileName = args.outFileName + '.tar.gz'
            with tarfile.open(args.outFileName, "w:gz") as tar_handle:
                for root, dirs, files in os.walk(bigwig_folder):
                    for file in files:
                        # tar_handle.addfile(tarfile.TarInfo(file), os.path.join(root, file))
                        # tar_handle.add(os.path.join(root, file))
                        tar_handle.add(os.path.join(root, file), arcname=file)

            if os.path.exists(bigwig_folder):
                try:
                    shutil.rmtree(bigwig_folder)
                except OSError as e:
                    log.error("Error: %s - %s." % (e.filename, e.strerror))
