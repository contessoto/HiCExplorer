import warnings
warnings.simplefilter(action="ignore", category=RuntimeWarning)
warnings.simplefilter(action="ignore", category=PendingDeprecationWarning)
import argparse

from scipy.sparse import csr_matrix, lil_matrix
import numpy as np

from hicmatrix import HiCMatrix as hm
from hicexplorer._version import __version__
from hicexplorer.utilities import obs_exp_matrix_lieberman, obs_exp_matrix_non_zero, obs_exp_matrix
from hicexplorer.utilities import convertNansToZeros, convertInfsToZeros


import logging
log = logging.getLogger(__name__)


def parse_arguments(args=None):
    """
    get command line arguments
    """
    parser = argparse.ArgumentParser(
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
        add_help=False,
        description='Converts the (interaction) matrix to different types of obs/exp, pearson or covariance matrix.')

    parserRequired = parser.add_argument_group('Required arguments')

    # define the arguments
    parserRequired.add_argument('--matrix', '-m',
                                help='input file. The computation is done per chromosome.',
                                required=True)

    parserRequired.add_argument('--outFileName', '-o',
                                help='File name to save the exported matrix.',
                                required=True)

    parserOpt = parser.add_argument_group('Optional arguments')

    parserOpt.add_argument('--method', '-me',
                           help='Transformation methods to use for input matrix. '
                           'Transformation is computed per chromosome.'
                           'obs_exp computes the expected matrix as the sum per '
                           'genomic distance j divided by maximal possible contacts: '
                           'sum(diagonal(j) / number of elements in diagonal(j) '
                           'obs_exp_lieberman computes the expected matrix as '
                           'the sum per genomic distance j divided by the : '
                           'sum(diagonal(j) / (length of chromosome - j))'
                           'obs_exp_non_zero computes the expected matrix as '
                           'the sum per genomic distance j divided by sum of '
                           'non-zero contacts: sum(diagonal(j) / number of non-zero elements in diagonal(j)'
                           'Optionally, ``--ligation_factor` can be used for this '
                           'method as has been used by HOMER software. If --ligation_factor, '
                           'then exp_i,j = exp_i,j * sum(row(i)) * sum(row(j)) / sum(matrix)'
                           'pearson computes the Pearson correlation of '
                           'the input matrix: Pearson_i,j = C_i,j / sqrt(C_i,i * C_j,j) '
                           'and C is the covariance matrix'
                           'covariance computes the Covariance of the '
                           'input matrix: Cov_i,j = E[M_i, M_j] - my_i * my_j '
                           'where M is the input matrix and my the mean'
                           ' (Default: %(default)s).',
                           choices=['obs_exp', 'obs_exp_lieberman', 'obs_exp_non_zero', 'pearson', 'covariance'],
                           default='obs_exp')

    parserOpt.add_argument('--ligation_factor',
                           help="Setting this flag, multiplies a scaling factor "
                           "to each entry of the expected matrix to take care "
                           "of the proximity ligation as has been explained "
                           "in Homer software. This flag is only affective "
                           "with obs_exp_non_zero method and will be ignored if "
                           "any other obs/exp method is chosen.",
                           action='store_true')

    parserOpt.add_argument('--chromosomes',
                           help='List of chromosomes to be included in the computation.',
                           default=None,
                           nargs='+')
    parserOpt.add_argument('--perChromosome', '-pc',
                           help='Each chromosome is processed individually, '
                           'inter-chromosomal interactions are ignored. Option '
                           'not valid for obs_exp_lieberman.',
                           action='store_true')

    parserOpt.add_argument("--help", "-h", action="help", help="Show this help message and exit.")

    parserOpt.add_argument('--version', action='version',
                           version='%(prog)s {}'.format(__version__))

    return parser


def _obs_exp_lieberman(pSubmatrix, pLengthChromosome, pChromosomeCount):

    obs_exp_matrix_ = obs_exp_matrix_lieberman(pSubmatrix, pLengthChromosome, pChromosomeCount)
    obs_exp_matrix_ = convertNansToZeros(csr_matrix(obs_exp_matrix_))
    obs_exp_matrix_ = convertInfsToZeros(csr_matrix(obs_exp_matrix_))
    # if len(obs_exp_matrix_.data) == 0:
    #     return np.array()
    return obs_exp_matrix_  # .todense()


def _pearson(pSubmatrix):
    pearson_correlation_matrix = np.corrcoef(pSubmatrix)
    pearson_correlation_matrix = convertNansToZeros(csr_matrix(pearson_correlation_matrix))
    pearson_correlation_matrix = convertInfsToZeros(csr_matrix(pearson_correlation_matrix))
    # if len(pearson_correlation_matrix.data) == 0:
    # return np.array([[]])
    return pearson_correlation_matrix  # .todense()


def _obs_exp(pSubmatrix):

    obs_exp_matrix_ = obs_exp_matrix(pSubmatrix)
    obs_exp_matrix_ = convertNansToZeros(csr_matrix(obs_exp_matrix_))
    obs_exp_matrix_ = convertInfsToZeros(csr_matrix(obs_exp_matrix_))
    # log.error('obs_exp_matrix_.data {}'.format(obs_exp_matrix_.data))
    # if len(obs_exp_matrix_.data) == 0:
    #     log.debug('No data!')
    #     return np.array([[]])
    return obs_exp_matrix_  # .todense()


def _obs_exp_non_zero(pSubmatrix, ligation_factor):

    obs_exp_matrix_ = obs_exp_matrix_non_zero(pSubmatrix, ligation_factor)
    obs_exp_matrix_ = convertNansToZeros(csr_matrix(obs_exp_matrix_))
    obs_exp_matrix_ = convertInfsToZeros(csr_matrix(obs_exp_matrix_))
    # if len(obs_exp_matrix_.data) == 0:
    # return np.array([[]])
    return obs_exp_matrix_  # .todense()


def main(args=None):

    args = parse_arguments().parse_args(args)

    if not args.outFileName.endswith('.h5') and not args.outFileName.endswith('.cool'):
        log.error('Output filetype not known.')
        log.error('It is: {}'.format(args.outFileName))
        log.error('Accepted is .h5 or .cool')
        exit(1)

    if args.matrix.endswith('cool') and args.chromosomes is not None and len(args.chromosomes) == 1:
        hic_ma = hm.hiCMatrix(pMatrixFile=args.matrix, pChrnameList=args.chromosomes)
    else:
        hic_ma = hm.hiCMatrix(pMatrixFile=args.matrix)
        if args.chromosomes:
            hic_ma.keepOnlyTheseChr(args.chromosomes)

    trasf_matrix = lil_matrix(hic_ma.matrix.shape)

    if args.method == 'obs_exp':
        if args.perChromosome:

            for chrname in hic_ma.getChrNames():
                chr_range = hic_ma.getChrBinRange(chrname)
                submatrix = hic_ma.matrix[chr_range[0]:chr_range[1], chr_range[0]:chr_range[1]]
                submatrix.astype(float)
                submatrix_chr = _obs_exp(submatrix)
                if len(submatrix_chr.data) == 0:
                    submatrix_chr = lil_matrix(submatrix_chr.shape)
                else:
                    submatrix_chr = lil_matrix(submatrix_chr)
                trasf_matrix[chr_range[0]:chr_range[1], chr_range[0]:chr_range[1]] = submatrix_chr
        else:
            submatrix = _obs_exp(hic_ma.matrix)
            trasf_matrix = csr_matrix(submatrix)

    elif args.method == 'obs_exp_non_zero':
        if args.perChromosome:

            for chrname in hic_ma.getChrNames():
                chr_range = hic_ma.getChrBinRange(chrname)
                submatrix = hic_ma.matrix[chr_range[0]:chr_range[1], chr_range[0]:chr_range[1]]
                submatrix.astype(float)

                submatrix_chr = _obs_exp_non_zero(submatrix, args.ligation_factor)
                if len(submatrix_chr.data) == 0:
                    submatrix_chr = lil_matrix(submatrix_chr.shape)
                else:
                    submatrix_chr = lil_matrix(submatrix_chr)
                trasf_matrix[chr_range[0]:chr_range[1], chr_range[0]:chr_range[1]] = submatrix_chr
        else:
            submatrix = _obs_exp_non_zero(hic_ma.matrix, args.ligation_factor)
            trasf_matrix = csr_matrix(submatrix)
    elif args.method == 'obs_exp_lieberman':
        length_chromosome = 0
        chromosome_count = len(hic_ma.getChrNames())
        for chrname in hic_ma.getChrNames():
            chr_range = hic_ma.getChrBinRange(chrname)
            length_chromosome += chr_range[1] - chr_range[0]
        for chrname in hic_ma.getChrNames():
            chr_range = hic_ma.getChrBinRange(chrname)
            submatrix = hic_ma.matrix[chr_range[0]:chr_range[1], chr_range[0]:chr_range[1]]
            submatrix.astype(float)

            submatrix_chr = _obs_exp_lieberman(submatrix, length_chromosome, chromosome_count)
            if len(submatrix_chr.data) == 0:
                submatrix_chr = lil_matrix(submatrix_chr.shape)
            else:
                submatrix_chr = lil_matrix(submatrix_chr)
            trasf_matrix[chr_range[0]:chr_range[1], chr_range[0]:chr_range[1]] = submatrix_chr
        trasf_matrix = trasf_matrix.tocsr()
        # log.debug('type: {}'.format(type(trasf_matrix)))
    elif args.method == 'pearson':
        if args.perChromosome:
            for chrname in hic_ma.getChrNames():
                chr_range = hic_ma.getChrBinRange(chrname)
                submatrix = hic_ma.matrix[chr_range[0]:chr_range[1], chr_range[0]:chr_range[1]]

                submatrix.astype(float)

                submatrix_chr = _pearson(submatrix.todense())
                if len(submatrix_chr.data) == 0:
                    submatrix_chr = lil_matrix(submatrix_chr.shape)
                else:
                    submatrix_chr = lil_matrix(submatrix_chr)

                trasf_matrix[chr_range[0]:chr_range[1], chr_range[0]:chr_range[1]] = submatrix_chr
        else:
            trasf_matrix = csr_matrix(_pearson(hic_ma.matrix.todense()))

    elif args.method == 'covariance':
        if args.perChromosome:

            for chrname in hic_ma.getChrNames():
                chr_range = hic_ma.getChrBinRange(chrname)
                submatrix = hic_ma.matrix[chr_range[0]:chr_range[1], chr_range[0]:chr_range[1]]

                submatrix.astype(float)

                # corrmatrix =

                submatrix_chr = np.cov(submatrix.todense())
                if len(submatrix_chr.data) == 0:
                    submatrix_chr = lil_matrix(submatrix_chr.shape)
                else:
                    submatrix_chr = lil_matrix(submatrix_chr)
                trasf_matrix[chr_range[0]:chr_range[1], chr_range[0]:chr_range[1]] = submatrix_chr
        else:
            corrmatrix = np.cov(hic_ma.matrix.todense())
            trasf_matrix = csr_matrix(corrmatrix)

    # log.debug('trasf_matrix {}'.format(trasf_matrix))

    if args.perChromosome:
        hic_ma.setMatrix(trasf_matrix.tocsr(), cut_intervals=hic_ma.cut_intervals)
    else:

        hic_ma.setMatrix(trasf_matrix, cut_intervals=hic_ma.cut_intervals)

    hic_ma.save(args.outFileName, pSymmetric=True, pApplyCorrection=False)
