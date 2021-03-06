#!/usr/bin/env python
# Copyright 2017 the V8 project authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""
Global system tests for V8 test runners and fuzzers.

This hooks up the framework under tools/testrunner testing high-level scenarios
with different test suite extensions and build configurations.
"""

# TODO(machenbach): Mock out util.GuessOS to make these tests really platform
# independent.
# TODO(machenbach): Move coverage recording to a global test entry point to
# include other unittest suites in the coverage report.
# TODO(machenbach): Coverage data from multiprocessing doesn't work.
# TODO(majeski): Add some tests for the fuzzers.

import collections
import contextlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

from cStringIO import StringIO

TOOLS_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEST_DATA_ROOT = os.path.join(TOOLS_ROOT, 'unittests', 'testdata')
RUN_TESTS_PY = os.path.join(TOOLS_ROOT, 'run-tests.py')

Result = collections.namedtuple(
    'Result', ['stdout', 'stderr', 'returncode'])

Result.__str__ = lambda self: (
    '\nReturncode: %s\nStdout:\n%s\nStderr:\n%s\n' %
    (self.returncode, self.stdout, self.stderr))


@contextlib.contextmanager
def temp_dir():
  """Wrapper making a temporary directory available."""
  path = None
  try:
    path = tempfile.mkdtemp('v8_test_')
    yield path
  finally:
    if path:
      shutil.rmtree(path)


@contextlib.contextmanager
def temp_base(baseroot='testroot1'):
  """Wrapper that sets up a temporary V8 test root.

  Args:
    baseroot: The folder with the test root blueprint. Relevant files will be
        copied to the temporary test root, to guarantee a fresh setup with no
        dirty state.
  """
  basedir = os.path.join(TEST_DATA_ROOT, baseroot)
  with temp_dir() as tempbase:
    builddir = os.path.join(tempbase, 'out', 'Release')
    testroot = os.path.join(tempbase, 'test')
    os.makedirs(builddir)
    shutil.copy(os.path.join(basedir, 'v8_build_config.json'), builddir)
    shutil.copy(os.path.join(basedir, 'd8_mocked.py'), builddir)

    for suite in os.listdir(os.path.join(basedir, 'test')):
      os.makedirs(os.path.join(testroot, suite))
      for entry in os.listdir(os.path.join(basedir, 'test', suite)):
        shutil.copy(
            os.path.join(basedir, 'test', suite, entry),
            os.path.join(testroot, suite))
    yield tempbase


@contextlib.contextmanager
def capture():
  """Wrapper that replaces system stdout/stderr an provides the streams."""
  oldout = sys.stdout
  olderr = sys.stderr
  try:
    stdout=StringIO()
    stderr=StringIO()
    sys.stdout = stdout
    sys.stderr = stderr
    yield stdout, stderr
  finally:
    sys.stdout = oldout
    sys.stderr = olderr


def run_tests(basedir, *args, **kwargs):
  """Executes the test runner with captured output."""
  with capture() as (stdout, stderr):
    sys_args = ['--command-prefix', sys.executable] + list(args)
    if kwargs.get('infra_staging', False):
      sys_args.append('--infra-staging')
    code = standard_runner.StandardTestRunner(
        basedir=basedir).execute(sys_args)
    return Result(stdout.getvalue(), stderr.getvalue(), code)


def override_build_config(basedir, **kwargs):
  """Override the build config with new values provided as kwargs."""
  path = os.path.join(basedir, 'out', 'Release', 'v8_build_config.json')
  with open(path) as f:
    config = json.load(f)
    config.update(kwargs)
  with open(path, 'w') as f:
    json.dump(config, f)


class SystemTest(unittest.TestCase):
  @classmethod
  def setUpClass(cls):
    # Try to set up python coverage and run without it if not available.
    cls._cov = None
    try:
      import coverage
      if int(coverage.__version__.split('.')[0]) < 4:
        cls._cov = None
        print 'Python coverage version >= 4 required.'
        raise ImportError()
      cls._cov = coverage.Coverage(
          source=([os.path.join(TOOLS_ROOT, 'testrunner')]),
          omit=['*unittest*', '*__init__.py'],
      )
      cls._cov.exclude('raise NotImplementedError')
      cls._cov.exclude('if __name__ == .__main__.:')
      cls._cov.exclude('except TestRunnerError:')
      cls._cov.exclude('except KeyboardInterrupt:')
      cls._cov.exclude('if options.verbose:')
      cls._cov.exclude('if verbose:')
      cls._cov.exclude('pass')
      cls._cov.exclude('assert False')
      cls._cov.start()
    except ImportError:
      print 'Running without python coverage.'
    sys.path.append(TOOLS_ROOT)
    global standard_runner
    from testrunner import standard_runner
    from testrunner.local import pool
    pool.setup_testing()

  @classmethod
  def tearDownClass(cls):
    if cls._cov:
      cls._cov.stop()
      print ''
      print cls._cov.report(show_missing=True)

  def testPass(self):
    """Test running only passing tests in two variants.

    Also test printing durations.
    """
    with temp_base() as basedir:
      result = run_tests(
          basedir,
          '--mode=Release',
          '--progress=verbose',
          '--variants=default,stress',
          '--time',
          'sweet/bananas',
          'sweet/raspberries',
      )
      self.assertIn('Running 4 tests', result.stdout, result)
      self.assertIn('Done running sweet/bananas: pass', result.stdout, result)
      self.assertIn('Total time:', result.stderr, result)
      self.assertIn('sweet/bananas', result.stderr, result)
      self.assertEqual(0, result.returncode, result)

  def testSharded(self):
    """Test running a particular shard."""
    with temp_base() as basedir:
      for shard in [1, 2]:
        result = run_tests(
            basedir,
            '--mode=Release',
            '--progress=verbose',
            '--variants=default,stress',
            '--shard-count=2',
            '--shard-run=%d' % shard,
            'sweet/bananas',
            'sweet/raspberries',
        )
        # One of the shards gets one variant of each test.
        self.assertIn('Running 2 tests', result.stdout, result)
        self.assertIn('Done running sweet/bananas', result.stdout, result)
        self.assertIn('Done running sweet/raspberries', result.stdout, result)
        self.assertEqual(0, result.returncode, result)

  def testFailProc(self):
    self.testFail(infra_staging=True)

  def testFail(self, infra_staging=False):
    """Test running only failing tests in two variants."""
    with temp_base() as basedir:
      result = run_tests(
          basedir,
          '--mode=Release',
          '--progress=verbose',
          '--variants=default,stress',
          'sweet/strawberries',
          infra_staging=infra_staging,
      )
      if infra_staging:
        self.assertIn('Running 1 tests', result.stdout, result)
      else:
        self.assertIn('Running 2 tests', result.stdout, result)
      self.assertIn('Done running sweet/strawberries: FAIL', result.stdout, result)
      self.assertEqual(1, result.returncode, result)

  def testFailWithRerunAndJSONProc(self):
    self.testFailWithRerunAndJSON(infra_staging=True)

  def testFailWithRerunAndJSON(self, infra_staging=False):
    """Test re-running a failing test and output to json."""
    with temp_base() as basedir:
      json_path = os.path.join(basedir, 'out.json')
      result = run_tests(
          basedir,
          '--mode=Release',
          '--progress=verbose',
          '--variants=default',
          '--rerun-failures-count=2',
          '--random-seed=123',
          '--json-test-results', json_path,
          'sweet/strawberries',
          infra_staging=infra_staging,
      )
      self.assertIn('Running 1 tests', result.stdout, result)
      self.assertIn('Done running sweet/strawberries: FAIL', result.stdout, result)
      if not infra_staging:
        # We run one test, which fails and gets re-run twice.
        self.assertIn('3 tests failed', result.stdout, result)
      else:
        # With test processors we don't count reruns as separated failures.
        # TODO(majeski): fix it.
        self.assertIn('1 tests failed', result.stdout, result)
      self.assertEqual(0, result.returncode, result)

      # Check relevant properties of the json output.
      with open(json_path) as f:
        json_output = json.load(f)[0]
        pretty_json = json.dumps(json_output, indent=2, sort_keys=True)

      # Replace duration in actual output as it's non-deterministic. Also
      # replace the python executable prefix as it has a different absolute
      # path dependent on where this runs.
      def replace_variable_data(data):
        data['duration'] = 1
        data['command'] = ' '.join(
            ['/usr/bin/python'] + data['command'].split()[1:])
      for data in json_output['slowest_tests']:
        replace_variable_data(data)
      for data in json_output['results']:
        replace_variable_data(data)
      json_output['duration_mean'] = 1

      suffix = ''
      if infra_staging:
        suffix = '-proc'
      expected_results_name = 'expected_test_results1%s.json' % suffix
      with open(os.path.join(TEST_DATA_ROOT, expected_results_name)) as f:
        expected_test_results = json.load(f)

      # TODO(majeski): Previously we only reported the variant flags in the
      # flags field of the test result.
      # After recent changes we report all flags, including the file names.
      # This is redundant to the command. Needs investigation.
      self.assertEqual(json_output, expected_test_results, pretty_json)

  def testAutoDetect(self):
    """Fake a build with several auto-detected options.

    Using all those options at once doesn't really make much sense. This is
    merely for getting coverage.
    """
    with temp_base() as basedir:
      override_build_config(
          basedir, dcheck_always_on=True, is_asan=True, is_cfi=True,
          is_msan=True, is_tsan=True, is_ubsan_vptr=True, target_cpu='x86',
          v8_enable_i18n_support=False, v8_target_cpu='x86',
          v8_use_snapshot=False)
      result = run_tests(
          basedir,
          '--mode=Release',
          '--progress=verbose',
          '--variants=default',
          'sweet/bananas',
      )
      expect_text = (
          '>>> Autodetected:\n'
          'asan\n'
          'cfi_vptr\n'
          'dcheck_always_on\n'
          'msan\n'
          'no_i18n\n'
          'no_snap\n'
          'tsan\n'
          'ubsan_vptr\n'
          '>>> Running tests for ia32.release')
      self.assertIn(expect_text, result.stdout, result)
      self.assertEqual(0, result.returncode, result)
      # TODO(machenbach): Test some more implications of the auto-detected
      # options, e.g. that the right env variables are set.

  def testSkipsProc(self):
    self.testSkips(infra_staging=True)

  def testSkips(self, infra_staging=False):
    """Test skipping tests in status file for a specific variant."""
    with temp_base() as basedir:
      result = run_tests(
          basedir,
          '--mode=Release',
          '--progress=verbose',
          '--variants=nooptimization',
          'sweet/strawberries',
          infra_staging=infra_staging,
      )
      self.assertIn('Running 0 tests', result.stdout, result)
      self.assertEqual(0, result.returncode, result)

  def testDefaultProc(self):
    self.testDefault(infra_staging=True)

  def testDefault(self, infra_staging=False):
    """Test using default test suites, though no tests are run since they don't
    exist in a test setting.
    """
    with temp_base() as basedir:
      result = run_tests(
          basedir,
          '--mode=Release',
          infra_staging=infra_staging,
      )
      self.assertIn('Warning: no tests were run!', result.stdout, result)
      self.assertEqual(0, result.returncode, result)

  def testNoBuildConfig(self):
    """Test failing run when build config is not found."""
    with temp_base() as basedir:
      result = run_tests(basedir)
      self.assertIn('Failed to load build config', result.stdout, result)
      self.assertEqual(1, result.returncode, result)

  def testGNOption(self):
    """Test using gn option, but no gn build folder is found."""
    with temp_base() as basedir:
      # TODO(machenbach): This should fail gracefully.
      with self.assertRaises(OSError):
        run_tests(basedir, '--gn')

  def testInconsistentMode(self):
    """Test failing run when attempting to wrongly override the mode."""
    with temp_base() as basedir:
      override_build_config(basedir, is_debug=True)
      result = run_tests(basedir, '--mode=Release')
      self.assertIn('execution mode (release) for release is inconsistent '
                    'with build config (debug)', result.stdout, result)
      self.assertEqual(1, result.returncode, result)

  def testInconsistentArch(self):
    """Test failing run when attempting to wrongly override the arch."""
    with temp_base() as basedir:
      result = run_tests(basedir, '--mode=Release', '--arch=ia32')
      self.assertIn(
          '--arch value (ia32) inconsistent with build config (x64).',
          result.stdout, result)
      self.assertEqual(1, result.returncode, result)

  def testWrongVariant(self):
    """Test using a bogus variant."""
    with temp_base() as basedir:
      result = run_tests(basedir, '--mode=Release', '--variants=meh')
      self.assertEqual(1, result.returncode, result)

  def testModeFromBuildConfig(self):
    """Test auto-detection of mode from build config."""
    with temp_base() as basedir:
      result = run_tests(basedir, '--outdir=out/Release', 'sweet/bananas')
      self.assertIn('Running tests for x64.release', result.stdout, result)
      self.assertEqual(0, result.returncode, result)

  def testReport(self):
    """Test the report feature.

    This also exercises various paths in statusfile logic.
    """
    with temp_base() as basedir:
      result = run_tests(
          basedir,
          '--mode=Release',
          '--variants=default',
          'sweet',
          '--report',
      )
      self.assertIn(
          '3 tests are expected to fail that we should fix',
          result.stdout, result)
      self.assertEqual(1, result.returncode, result)

  def testWarnUnusedRules(self):
    """Test the unused-rules feature."""
    with temp_base() as basedir:
      result = run_tests(
          basedir,
          '--mode=Release',
          '--variants=default,nooptimization',
          'sweet',
          '--warn-unused',
      )
      self.assertIn( 'Unused rule: carrots', result.stdout, result)
      self.assertIn( 'Unused rule: regress/', result.stdout, result)
      self.assertEqual(1, result.returncode, result)

  def testCatNoSources(self):
    """Test printing sources, but the suite's tests have none available."""
    with temp_base() as basedir:
      result = run_tests(
          basedir,
          '--mode=Release',
          '--variants=default',
          'sweet/bananas',
          '--cat',
      )
      self.assertIn('begin source: sweet/bananas', result.stdout, result)
      self.assertIn('(no source available)', result.stdout, result)
      self.assertEqual(0, result.returncode, result)

  def testPredictableProc(self):
    self.testPredictable(infra_staging=True)

  def testPredictable(self, infra_staging=False):
    """Test running a test in verify-predictable mode.

    The test will fail because of missing allocation output. We verify that and
    that the predictable flags are passed and printed after failure.
    """
    with temp_base() as basedir:
      override_build_config(basedir, v8_enable_verify_predictable=True)
      result = run_tests(
          basedir,
          '--mode=Release',
          '--progress=verbose',
          '--variants=default',
          'sweet/bananas',
          infra_staging=infra_staging,
      )
      self.assertIn('Running 1 tests', result.stdout, result)
      self.assertIn('Done running sweet/bananas: FAIL', result.stdout, result)
      self.assertIn('Test had no allocation output', result.stdout, result)
      self.assertIn('--predictable --verify_predictable', result.stdout, result)
      self.assertEqual(1, result.returncode, result)

  def testSlowArch(self):
    """Test timeout factor manipulation on slow architecture."""
    with temp_base() as basedir:
      override_build_config(basedir, v8_target_cpu='arm64')
      result = run_tests(
          basedir,
          '--mode=Release',
          '--progress=verbose',
          '--variants=default',
          'sweet/bananas',
      )
      # TODO(machenbach): We don't have a way for testing if the correct
      # timeout was used.
      self.assertEqual(0, result.returncode, result)

  def testRandomSeedStressWithDefault(self):
    """Test using random-seed-stress feature has the right number of tests."""
    with temp_base() as basedir:
      result = run_tests(
          basedir,
          '--mode=Release',
          '--progress=verbose',
          '--variants=default',
          '--random-seed-stress-count=2',
          'sweet/bananas',
      )
      self.assertIn('Running 2 tests', result.stdout, result)
      self.assertEqual(0, result.returncode, result)

  def testRandomSeedStressWithSeed(self):
    """Test using random-seed-stress feature passing a random seed."""
    with temp_base() as basedir:
      result = run_tests(
          basedir,
          '--mode=Release',
          '--progress=verbose',
          '--variants=default',
          '--random-seed-stress-count=2',
          '--random-seed=123',
          'sweet/strawberries',
      )
      self.assertIn('Running 2 tests', result.stdout, result)
      # We use a failing test so that the command is printed and we can verify
      # that the right random seed was passed.
      self.assertIn('--random-seed=123', result.stdout, result)
      self.assertEqual(1, result.returncode, result)

  def testSpecificVariants(self):
    """Test using NO_VARIANTS and FAST_VARIANTS modifiers in status files skips
    the desire tests.

    The test runner cmd line configures 4 tests to run (2 tests * 2 variants).
    But the status file applies a modifier to each skipping one of the
    variants.
    """
    with temp_base() as basedir:
      override_build_config(basedir, v8_use_snapshot=False)
      result = run_tests(
          basedir,
          '--mode=Release',
          '--progress=verbose',
          '--variants=default,stress',
          'sweet/bananas',
          'sweet/raspberries',
      )
      # Both tests are either marked as running in only default or only
      # slow variant.
      self.assertIn('Running 2 tests', result.stdout, result)
      self.assertEqual(0, result.returncode, result)

  def testStatusFilePresubmit(self):
    """Test that the fake status file is well-formed."""
    with temp_base() as basedir:
      from testrunner.local import statusfile
      self.assertTrue(statusfile.PresubmitCheck(
          os.path.join(basedir, 'test', 'sweet', 'sweet.status')))

  def testDotsProgressProc(self):
    self.testDotsProgress(infra_staging=True)

  def testDotsProgress(self, infra_staging=False):
    with temp_base() as basedir:
      result = run_tests(
          basedir,
          '--mode=Release',
          '--progress=dots',
          'sweet/cherries',
          'sweet/bananas',
          '--no-sorting', '-j1', # make results order deterministic
          infra_staging=infra_staging,
      )
      self.assertIn('Running 2 tests', result.stdout, result)
      self.assertIn('F.', result.stdout, result)
      self.assertEqual(1, result.returncode, result)

  def testMonoProgressProc(self):
    self._testCompactProgress('mono', True)

  def testMonoProgress(self):
    self._testCompactProgress('mono', False)

  def testColorProgressProc(self):
    self._testCompactProgress('color', True)

  def testColorProgress(self):
    self._testCompactProgress('color', False)

  def _testCompactProgress(self, name, infra_staging):
    with temp_base() as basedir:
      result = run_tests(
          basedir,
          '--mode=Release',
          '--progress=%s' % name,
          'sweet/cherries',
          'sweet/bananas',
          infra_staging=infra_staging,
      )
      if name == 'color':
        expected = ('\033[34m% 100\033[0m|'
                    '\033[32m+   1\033[0m|'
                    '\033[31m-   1\033[0m]: Done')
      else:
        expected = '% 100|+   1|-   1]: Done'
      self.assertIn(expected, result.stdout)
      self.assertIn('sweet/cherries', result.stdout)
      self.assertIn('sweet/bananas', result.stdout)
      self.assertEqual(1, result.returncode, result)


if __name__ == '__main__':
  unittest.main()
