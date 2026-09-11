"""Exercise the shipped installer and installed wheel outside the source checkout."""
import argparse
from functools import partial
import hashlib
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import tarfile
import tempfile
import threading
import time
from urllib.error import URLError
from urllib.request import urlopen
import zipfile

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--artifacts', type=Path, required=True, help='directory produced by scripts/build_downloads.py')
parser.add_argument('--output', type=Path, help='new evidence directory (default: ARTIFACTS/verification)')
args = parser.parse_args()
ROOT = args.artifacts.expanduser().resolve()
LOGS = args.output.expanduser().resolve() if args.output else ROOT / 'verification'
LOGS.mkdir(parents=True, exist_ok=False)
release = json.loads((ROOT / 'release.json').read_text())
VERSION = release['version']
WHEEL, = (Path(item['path']).name for item in release['artifacts'] if item['path'].endswith('.whl'))
SDIST, = (Path(item['path']).name for item in release['artifacts'] if item['path'].endswith('.tar.gz'))
results = []


class Artifacts(SimpleHTTPRequestHandler):
    tamper = False
    requests = 0

    def log_message(self, *_args):
        pass

    def do_GET(self):
        type(self).requests += 1
        if self.tamper and self.path.endswith('.whl'):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'damaged artifact')
        else:
            super().do_GET()


def record(name, **details):
    results.append({'check': name, 'passed': True, **details})
    print('PASS ' + name, flush=True)


def free_port():
    with socket.socket() as connection:
        connection.bind(('127.0.0.1', 0))
        return connection.getsockname()[1]


def main():
    for artifact in release['artifacts']:
        path = ROOT / artifact['path']
        assert path.resolve().is_relative_to(ROOT), path
        assert hashlib.sha256(path.read_bytes()).hexdigest() == artifact['sha256'], path
    record('generated artifact hashes match release manifest')
    with zipfile.ZipFile(ROOT / 'downloads' / WHEEL) as wheel:
        assets = {name: wheel.read(name) for name in wheel.namelist() if name.startswith('respawned/web_assets/') and not name.endswith('/')}
        assert assets['respawned/web_assets/index.html']
        assert assets['respawned/web_assets/bundle-manifest.json']
        metadata = wheel.read('respawned-' + VERSION + '.dist-info/METADATA').decode()
        assert 'Requires-Python: >=3.12' in metadata
        with tarfile.open(ROOT / 'downloads' / SDIST) as source:
            sdist_prefix = source.getnames()[0].split('/')[0]
            for name, content in assets.items():
                archived = source.extractfile(sdist_prefix + '/src/' + name)
                assert archived is not None and archived.read() == content
            assert source.getmember(sdist_prefix + '/web/package-lock.json')
        record('wheel and sdist contain identical bundled UI', asset_files=len(assets))

    server = ThreadingHTTPServer(('127.0.0.1', 0), partial(Artifacts, directory=str(ROOT)))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    origin = 'http://127.0.0.1:' + str(server.server_port)
    try:
        with tempfile.TemporaryDirectory(prefix='respawned-distribution-check-') as temp:
            workspace = Path(temp)
            env = {key: value for key, value in os.environ.items() if not key.startswith(('PYTHON', 'PIP_', 'RESPAWNED_', 'DB_'))}
            env['HOME'] = str(workspace / 'home')
            Path(env['HOME']).mkdir()
            prefix = workspace / 'install with spaces'
            installer = workspace / 'install.sh'
            installer.write_bytes(urlopen(origin + '/install.sh', timeout=10).read())

            def install(name, target=prefix, expected=0, extra=None):
                command = ['sh', str(installer), '--prefix', str(target), '--test-base-url', origin]
                if extra:
                    command.extend(extra)
                completed = subprocess.run(command, cwd=workspace, env=env, capture_output=True, text=True, timeout=750)
                (LOGS / (name + '.log')).write_text(completed.stdout + completed.stderr)
                assert completed.returncode == expected, (name, completed.stdout[-3000:], completed.stderr[-3000:])
                return completed

            original = workspace / 'unrelated-command'
            (original / 'bin').mkdir(parents=True)
            sentinel = original / 'bin' / 'respawned'
            sentinel.write_text('existing command; do not replace\n')
            requests_before = Artifacts.requests
            blocked = install('unrelated-command', original, 1)
            assert 'Refusing to overwrite an unrelated command' in blocked.stderr
            assert sentinel.read_text() == 'existing command; do not replace\n'
            assert Artifacts.requests == requests_before
            record('unrelated launcher preserved before network access')

            unrelated = workspace / 'unrelated-directory'
            (unrelated / 'share' / 'respawned').mkdir(parents=True)
            untouched = unrelated / 'share' / 'respawned' / 'keep.txt'
            untouched.write_text('keep me')
            assert 'unrecognized installation directory' in install('unrelated-directory', unrelated, 1).stderr
            assert untouched.read_text() == 'keep me'
            record('unrelated installation directory preserved')

            corrupt = workspace / 'corrupt-download'
            Artifacts.tamper = True
            try:
                assert 'checksum mismatch' in install('checksum-mismatch', corrupt, 1).stderr
            finally:
                Artifacts.tamper = False
            assert not (corrupt / 'share' / 'respawned' / VERSION).exists()
            assert not (corrupt / 'bin' / 'respawned').exists()
            record('corrupt wheel rejected before creating environment')

            bad_origin = install('non-loopback-rejected', workspace / 'bad-origin', 1,
                                 ['--test-base-url', 'http://example.com'])
            assert 'loopback HTTP origin' in bad_origin.stderr
            record('test override rejects non-loopback HTTP')

            install('complete-install')
            command = prefix / 'bin' / 'respawned'
            assert command.is_symlink()
            assert command.resolve() == prefix / 'share' / 'respawned' / VERSION / 'bin' / 'respawned'
            record('downloaded installer installs wheel with dependencies under isolated prefix')

            requests_before = Artifacts.requests
            assert 'already installed' in install('repeat-install').stdout
            assert Artifacts.requests == requests_before
            record('repeat install is idempotent without another download')

            python = prefix / 'share' / 'respawned' / VERSION / 'bin' / 'python'
            package_paths = json.loads(subprocess.check_output([str(python), '-I', '-B', '-c',
                "import json; from importlib.metadata import distribution; "
                "from importlib.resources import files; package=distribution('respawned'); "
                "metadata=next(item for item in package.files if str(item).endswith('.dist-info/METADATA')); "
                "print(json.dumps({'package': str(files('respawned')), 'metadata': str(package.locate_file(metadata))}))"],
                cwd=workspace, env=env, text=True))
            retained_state = prefix / 'retained-user-state.json'
            retained_state.write_text('{"preserve": true}\n')
            install_receipt = python.parent.parent / 'respawned-install.json'

            def damaged_repeat(name, path, message, replacement=None):
                original, mode = path.read_bytes(), path.stat().st_mode & 0o7777
                launcher_before = (os.readlink(command), command.lstat().st_ino)
                receipt_before = install_receipt.read_bytes()
                requests_before = Artifacts.requests
                if replacement is None:
                    path.unlink()
                else:
                    path.write_bytes(replacement)
                try:
                    rejected = install(name, expected=1)
                    assert message in rejected.stderr
                    assert 'current launcher were left unchanged' in rejected.stderr
                    assert 'different --prefix' in rejected.stderr
                    assert 'already installed' not in rejected.stdout
                    assert (os.readlink(command), command.lstat().st_ino) == launcher_before
                    assert install_receipt.read_bytes() == receipt_before
                    assert retained_state.read_text() == '{"preserve": true}\n'
                    assert Artifacts.requests == requests_before
                    assert not (python.parent.parent.parent / '.install-lock').exists()
                    if replacement is None:
                        assert not path.exists()
                    else:
                        assert path.read_bytes() == replacement
                finally:
                    # Restore only the temporary verification environment's test damage.
                    path.write_bytes(original)
                    path.chmod(mode)
                record(name + ' preserves retained installation and launcher without download')

            metadata_path = Path(package_paths['metadata'])
            damaged_repeat('repeat-missing-dependency', metadata_path, 'Dependency check failed',
                           metadata_path.read_bytes().split(b'\n\n', 1)[0]
                           + b'\nRequires-Dist: respawned-installer-missing-dependency==0.0.0\n\n')
            ui_path = Path(package_paths['package']) / 'web_assets'
            javascript = next(name.removeprefix('respawned/web_assets/') for name in assets if name.endswith('.js'))
            for name, relative in [('index', 'index.html'), ('manifest', 'bundle-manifest.json'), ('javascript', javascript)]:
                damaged_repeat('repeat-missing-ui-' + name, ui_path / relative, 'Bundled UI file is missing')
            damaged_repeat('repeat-changed-ui-javascript', ui_path / javascript,
                           'Bundled UI file does not match', b'damaged bundled JavaScript\n')

            requests_before = Artifacts.requests
            download = subprocess.Popen(['curl', '--fail', '--silent', '--show-error', origin + '/install.sh'],
                                        cwd=workspace, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            piped = subprocess.run(['sh', '-s', '--', '--prefix', str(prefix), '--test-base-url', origin],
                                   cwd=workspace, env=env, stdin=download.stdout,
                                   capture_output=True, text=True, timeout=120)
            download.stdout.close()
            download.wait(timeout=10)
            (LOGS / 'curl-pipe-install.log').write_text(piped.stdout + piped.stderr)
            assert download.returncode == 0 and piped.returncode == 0
            assert 'already installed' in piped.stdout
            assert Artifacts.requests == requests_before + 1
            record('curl pipe to sh accepts arguments and reuses the verified installation')

            shared = workspace / 'relocated share'
            (prefix / 'share').rename(shared)
            (prefix / 'share').symlink_to(shared, target_is_directory=True)
            try:
                assert 'already installed' in install('symlinked-share').stdout
            finally:
                (prefix / 'share').unlink()
                shared.rename(prefix / 'share')
            record('owned installation remains reinstallable through a symlinked share directory')

            for args in [['--version'], ['--help'], ['serve', '--help'], ['ui', '--help'], ['outbox', '--help']]:
                completed = subprocess.run([str(command), *args], cwd=workspace, env=env, capture_output=True, text=True, check=True)
                (LOGS / ('cli-' + '-'.join(part.strip('-') for part in args) + '.log')).write_text(completed.stdout)
                assert 'respawned' in completed.stdout.lower()
                if args == ['--version']:
                    assert completed.stdout.strip() == 'respawned ' + VERSION
            record('installed CLI version and command help work outside checkout', commands=5)

            installed_path = subprocess.check_output([str(python), '-c', 'import respawned; print(respawned.__path__[0])'],
                                                     cwd=workspace, env=env, text=True).strip()
            assert Path(installed_path).is_relative_to(prefix)
            for name, content in assets.items():
                relative = name.removeprefix('respawned/')
                assert (Path(installed_path) / relative).read_bytes() == content
            record('import and every bundled asset come from installed wheel', asset_files=len(assets))

            port = free_port()
            env.update(DB_HOST='127.0.0.1', DB_PORT='1', DB_NAME='unavailable', DB_USER='unavailable', DB_PASSWORD='unavailable')
            with (LOGS / 'bundled-server.log').open('w') as log:
                process = subprocess.Popen([str(command), 'serve', '--host', '127.0.0.1', '--port', str(port)],
                                           cwd=workspace, env=env, stdout=log, stderr=log)
                try:
                    app_origin = 'http://127.0.0.1:' + str(port)
                    for _ in range(100):
                        assert process.poll() is None, 'Installed server exited'
                        try:
                            health = urlopen(app_origin + '/healthz', timeout=.5)
                            assert health.status == 200
                            break
                        except URLError:
                            time.sleep(.1)
                    else:
                        raise AssertionError('Installed server did not start')
                    page = urlopen(app_origin, timeout=5).read()
                    assert page == assets['respawned/web_assets/index.html']
                    for asset in re.findall(r'(?:src|href)="(/assets/[^"]+)"', page.decode()):
                        assert urlopen(app_origin + asset, timeout=5).read() == assets['respawned/web_assets' + asset]
                    record('installed server serves bundled HTML, JS, CSS and liveness without database or Node')
                finally:
                    process.terminate()
                    process.wait(timeout=10)
            record('temporary install removed and owned server stopped on validation exit')
    finally:
        server.shutdown()
        server.server_close()

    (LOGS / 'results.json').write_text(json.dumps({'checks': results, 'count': len(results),
        'artifact_sha256': hashlib.sha256((ROOT / 'downloads' / WHEEL).read_bytes()).hexdigest(),
        'external_provider_calls': 0, 'database_writes': 0,
        'scope': 'Temporary HOME and prefix; installed dependencies from PyPI; no global install'}, indent=2) + '\n')
    release['validation']['installer_execution_verified'] = True
    release['validation']['installer_checks_passed'] = len(results)
    (ROOT / 'release.json').write_text(json.dumps(release, indent=2) + '\n')
    print(f'Finished {len(results)} checks. Logs: {LOGS}', flush=True)


if __name__ == '__main__':
    main()
