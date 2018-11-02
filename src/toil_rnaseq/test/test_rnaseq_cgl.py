import logging
import os
import posixpath
import shlex
import shutil
import subprocess
import tempfile
import textwrap
from contextlib import closing
from unittest import TestCase
from urlparse import urlparse
from uuid import uuid4

from bd2k.util.iterables import concat
from boto.s3.connection import S3Connection, Bucket

log = logging.getLogger(__name__)


class RNASeqCGLTest(TestCase):
    """
    These tests *can* be parameterized with the following optional environment variables:

    TOIL_SCRIPTS_TEST_TOIL_OPTIONS - a space-separated list of additional command line arguments to pass to Toil via
    the script entry point. Default is the empty string.

    TOIL_SCRIPTS_TEST_JOBSTORE - the job store locator to use for the tests. The default is a file: locator pointing
    at a local temporary directory.

    TOIL_SCRIPTS_TEST_NUM_SAMPLES - the number of sample lines to generate in the input manifest
    """

    @classmethod
    def setUpClass(cls):
        super(RNASeqCGLTest, cls).setUpClass()
        # FIXME: pull up into common base class
        logging.basicConfig(level=logging.INFO)

    def setUp(self):
        # S3 bucket link
        self.output_dir = urlparse('s3://cgl-driver-projects/test/ci/%s' % uuid4())
        # URLs to chr6 sample
        self.input_url = urlparse('http://courtyard.gi.ucsc.edu/~jvivian/toil-rnaseq-inputs/')
        self.sample = urlparse(os.path.join(self.input_url.geturl(), 'continuous_integration/chr6_paired.tar.gz'))
        self.bam_sample = urlparse(self.input_url.geturl() + 'continuous_integration/chr6.test.bam')
        # Command setup
        self.workdir = tempfile.mkdtemp()
        jobStore = os.getenv('TOIL_SCRIPTS_TEST_JOBSTORE', os.path.join(self.workdir, 'jobstore-%s' % uuid4()))
        toilOptions = shlex.split(os.environ.get('TOIL_SCRIPTS_TEST_TOIL_OPTIONS', ''))
        self.base_command = concat('toil-rnaseq', 'run',
                                   '--config', self._generate_config(),
                                   '--retryCount', '1',
                                   toilOptions,
                                   jobStore)

    def test_manifest(self):
        num_samples = int(os.environ.get('TOIL_SCRIPTS_TEST_NUM_SAMPLES', '1'))
        self._run(self.base_command, '--manifest', self._generate_manifest(num_samples=num_samples))
        self._assertOutput(num_samples)

    def test_bam(self):
        self._run(self.base_command, '--manifest', self._generate_manifest(bam=True))
        self._assertOutput(bam=True)

    def _run(self, *args):
        args = list(concat(*args))
        log.info('Running %r', args)
        subprocess.check_call(args)

    def _assertOutput(self, num_samples=None, bam=False):
        with closing(S3Connection()) as s3:
            bucket = Bucket(s3, self.output_dir.netloc)
            prefix = self.output_dir.path[1:]
            for i in range(1 if num_samples is None else num_samples):
                value = None if num_samples is None else i
                output_file = self._sample_name(value, bam=bam) + '.tar.gz'
                key = bucket.get_key(posixpath.join(prefix, output_file), validate=True)
                # FIXME: We may want to validate the output a bit more
                self.assertTrue(key.size > 0)

    def tearDown(self):
        shutil.rmtree(self.workdir)
        with closing(S3Connection()) as s3:
            bucket = Bucket(s3, self.output_dir.netloc)
            prefix = self.output_dir.path[1:]
            for key in bucket.list(prefix=prefix):
                assert key.name.startswith(prefix)
                key.delete()

    def _generate_config(self):
        path = os.path.join(self.workdir, 'config-toil-rnaseq.yaml')
        with open(path, 'w') as f:
            f.write(textwrap.dedent("""
                    star-index: {input_url}/continuous_integration/starIndex_chr6.tar.gz
                    rsem-ref: {input_url}/continuous_integration/rsem_ref_chr6.tar.gz
                    kallisto-index: {input_url}/kallisto_hg38.idx
                    hera-index: {input_url}/hera-index.tar.gz
                    output-dir: {output_dir}
                    max-sample-size: 2G
                    fastqc: true
                    cutadapt: true
                    ssec:
                    gdc-token:
                    wiggle:
                    save-bam:
                    fwd-3pr-adapter: AGATCGGAAGAG
                    rev-3pr-adapter: AGATCGGAAGAG
                    bamqc: true
                    ci-test: true
                    """[1:]).format(output_dir=self.output_dir.geturl(),
                                    input_url=self.input_url.geturl()))
        return path

    def _generate_manifest(self, num_samples=1, bam=False):
        path = os.path.join(self.workdir, 'manifest-toil-rnaseq.tsv')
        if bam:
            with open(path, 'w') as f:
                f.write('\t'.join(['bam', 'paired', 'chr6.test', self.bam_sample.geturl()]) + '\n')
        else:
            with open(path, 'w') as f:
                f.write('\n'.join('\t'.join(['tar', 'paired', self._sample_name(i), self.sample.geturl()])
                                  for i in range(num_samples)))
        return path

    def _sample_name(self, i=None, bam=False):
        if bam:
            uuid = posixpath.basename(self.bam_sample.path).split('.')
        else:
            uuid = posixpath.basename(self.sample.path).split('.')
        while uuid[-1] in ('gz', 'tar', 'zip', 'bam'):
            uuid.pop()
        uuid = '.'.join(uuid)
        if i is not None:
            uuid = '%s_%i' % (uuid, i)
        return uuid
