#!/usr/bin/env python3

import copy
from io import BytesIO
import json
import os
import selectors
from subprocess import (
    PIPE,
    Popen,
)
import sys
import tarfile
from threading import (
    Event,
    Thread,
)
import time

# See fcntl(2), re. F_SETPIPE_SZ. By requesting this many bytes from a pipe on
# each read we can be sure that we are always draining its buffer completely.
with open("/proc/sys/fs/pipe-max-size") as _pms:
    PIPE_MAX_SIZE = int(_pms.read())


try:
    from maas_api_helper import (
        geturl,
        MD_VERSION,
        read_config,
        signal,
        SignalException,
    )
except ImportError:
    # For running unit tests.
    from snippets.maas_api_helper import (
        geturl,
        MD_VERSION,
        read_config,
        signal,
        SignalException,
    )


def fail(msg):
    sys.stderr.write("FAIL: %s" % msg)
    sys.exit(1)


def signal_wrapper(*args, **kwargs):
    """Wrapper to output any SignalExceptions to STDERR."""
    try:
        signal(*args, **kwargs)
    except SignalException as e:
        fail(e.error)


def download_and_extract_tar(url, creds, scripts_dir):
    """Download and extract a tar from the given URL.

    The URL may contain a compressed or uncompressed tar.
    """
    binary = BytesIO(geturl(url, creds))

    with tarfile.open(mode='r|*', fileobj=binary) as tar:
        tar.extractall(scripts_dir)


def capture_script_output(proc, combined_path, stdout_path, stderr_path):
    """Capture stdout and stderr from `proc`.

    Standard output is written to a file named by `stdout_path`, and standard
    error is written to a file named by `stderr_path`. Both are also written
    to a file named by `combined_path`.

    If the given subprocess forks additional processes, and these write to the
    same stdout and stderr, their output will be captured only as long as
    `proc` is running.

    :return: The exit code of `proc`.
    """
    with open(stdout_path, 'wb') as out, open(stderr_path, 'wb') as err:
        with open(combined_path, 'wb') as combined:
            with selectors.DefaultSelector() as selector:
                selector.register(proc.stdout, selectors.EVENT_READ, out)
                selector.register(proc.stderr, selectors.EVENT_READ, err)
                while selector.get_map() and proc.poll() is None:
                    # Select with a short timeout so that we don't tight loop.
                    _select_script_output(selector, combined, 0.1)
                else:
                    # Process has finished or has closed stdout and stderr.
                    # Process anything still sitting in the latter's buffers.
                    _select_script_output(selector, combined, 0.0)

    # Always wait for the process to finish.
    return proc.wait()


def _select_script_output(selector, combined, timeout):
    """Helper for `capture_script_output`."""
    for key, event in selector.select(timeout):
        if event & selectors.EVENT_READ:
            # Read from the _raw_ file. Ordinarily Python blocks until a
            # read(n) returns n bytes or the stream reaches end-of-file,
            # but here we only want to get what's there without blocking.
            chunk = key.fileobj.raw.read(PIPE_MAX_SIZE)
            if len(chunk) == 0:  # EOF
                selector.unregister(key.fileobj)
            else:
                key.data.write(chunk)
                combined.write(chunk)


def run_scripts(url, creds, scripts_dir, out_dir, scripts):
    """Run and report results for the given scripts."""
    fail_count = 0
    base_args = {
        'url': url,
        'creds': creds,
        'status': 'WORKING',
    }
    for i, script in enumerate(scripts):
        i += 1
        args = copy.deepcopy(base_args)
        args['script_result_id'] = script['script_result_id']
        script_version_id = script.get('script_version_id')
        if script_version_id is not None:
            args['script_version_id'] = script_version_id

        signal_wrapper(
            **args, error='Starting %s [%d/%d]' % (
                script['name'], i, len(scripts)))

        # If we pipe the output of the subprocess and the subprocess also
        # creates a subprocess we end up dead locking. Spawn a shell process
        # and capture the output to the filesystem to avoid that and help with
        # debugging.
        script_path = os.path.join(scripts_dir, script['path'])
        combined_path = os.path.join(out_dir, script['name'])
        stdout_name = '%s.out' % script['name']
        stdout_path = os.path.join(out_dir, stdout_name)
        stderr_name = '%s.err' % script['name']
        stderr_path = os.path.join(out_dir, stderr_name)

        try:
            proc = Popen(script_path, stdout=PIPE, stderr=PIPE)
        except OSError as e:
            fail_count += 1
            if isinstance(e.errno, int) and e.errno != 0:
                args['exit_status'] = e.errno
            else:
                # 2 is the return code bash gives when it can't execute.
                args['exit_status'] = 2
            result = str(e).encode()
            if result == b'':
                result = b'Unable to execute script'
            args['files'] = {
                script['name']: result,
                stderr_name: result,
            }
        else:
            capture_script_output(
                proc, combined_path, stdout_path, stderr_path)

            if proc.returncode != 0:
                fail_count += 1
            args['exit_status'] = proc.returncode
            args['files'] = {
                script['name']: open(combined_path, 'rb').read(),
                stdout_name: open(stdout_path, 'rb').read(),
                stderr_name: open(stderr_path, 'rb').read(),
            }

        signal_wrapper(**args, error='Finished %s [%d/%d]: %d' % (
            script['name'], i, len(scripts), args['exit_status']))

    # Signal failure after running commissioning or testing scripts so MAAS
    # transisitions the node into FAILED_COMMISSIONING or FAILED_TESTING.
    if fail_count != 0:
        signal_wrapper(
            url, creds, 'FAILED', '%d scripts failed to run' % fail_count)

    return fail_count


def run_scripts_from_metadata(url, creds, scripts_dir, out_dir):
    """Run all scripts from a tar given by MAAS."""
    with open(os.path.join(scripts_dir, 'index.json')) as f:
        scripts = json.load(f)['1.0']

    fail_count = 0
    commissioning_scripts = scripts.get('commissioning_scripts')
    if commissioning_scripts is not None:
        fail_count += run_scripts(
            url, creds, scripts_dir, out_dir, commissioning_scripts)

    testing_scripts = scripts.get('testing_scripts')
    if testing_scripts is not None:
        # If the node status was COMMISSIONING transition the node into TESTING
        # status. If the node is already in TESTING status this is ignored.
        signal_wrapper(url, creds, 'TESTING')
        fail_count += run_scripts(
            url, creds, scripts_dir, out_dir, testing_scripts)

    # Only signal OK when we're done with everything and nothing has failed.
    if fail_count == 0:
        signal_wrapper(url, creds, 'OK', 'All scripts successfully ran')


class HeartBeat(Thread):
    """Creates a background thread which pings the MAAS metadata service every
    two minutes to let it know we're still up and running scripts. If MAAS
    doesn't hear from us it will assume something has gone wrong and power off
    the node.
    """

    def __init__(self, url, creds):
        super().__init__(name='HeartBeat')
        self._url = url
        self._creds = creds
        self._run = Event()
        self._run.set()

    def stop(self):
        self._run.clear()

    def run(self):
        while self._run.is_set():
            signal_wrapper(self._url, self._creds, 'WORKING')
            # Check every second if we should still be working. This ensures
            # we don't keep the process running unnecessarily for too long.
            for _ in range(120):
                if self._run.is_set():
                    time.sleep(1)


def main():
    import argparse

    parser = argparse.ArgumentParser(
        description='Download and run scripts from the MAAS metadata service.')
    parser.add_argument(
        "--config", metavar="file", help="Specify config file", default=None)
    parser.add_argument(
        "--ckey", metavar="key", help="The consumer key to auth with",
        default=None)
    parser.add_argument(
        "--tkey", metavar="key", help="The token key to auth with",
        default=None)
    parser.add_argument(
        "--csec", metavar="secret", help="The consumer secret (likely '')",
        default="")
    parser.add_argument(
        "--tsec", metavar="secret", help="The token secret to auth with",
        default=None)
    parser.add_argument(
        "--apiver", metavar="version",
        help="The apiver to use (\"\" can be used)", default=MD_VERSION)
    parser.add_argument(
        "--url", metavar="url", help="The data source to query", default=None)

    parser.add_argument(
        "storage_directory",
        help="Directory to store the extracted data from the metadata service."
    )

    args = parser.parse_args()

    creds = {
        'consumer_key': args.ckey,
        'token_key': args.tkey,
        'token_secret': args.tsec,
        'consumer_secret': args.csec,
        'metadata_url': args.url,
        }

    if args.config:
        read_config(args.config, creds)

    url = creds.get('metadata_url')
    if url is None:
        fail("URL must be provided either in --url or in config\n")
    url = "%s/%s/" % (url, args.apiver)

    heart_beat = HeartBeat(url, creds)
    heart_beat.start()

    scripts_dir = os.path.join(args.storage_directory, 'scripts')
    os.makedirs(scripts_dir)
    out_dir = os.path.join(args.storage_directory, 'out')
    os.makedirs(out_dir)

    download_and_extract_tar("%s/maas-scripts/" % url, creds, scripts_dir)
    run_scripts_from_metadata(url, creds, scripts_dir, out_dir)

    heart_beat.stop()


if __name__ == '__main__':
    main()