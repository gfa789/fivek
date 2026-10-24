#!/usr/bin/python3
"""Pull trusted main branches, build without production data, and switch releases."""
import argparse
import fcntl
import json
import os
from pathlib import Path
import pwd
import re
import sqlite3
import subprocess
import sys
import time
import urllib.request

STATE = Path('/var/lib/personal-apps-deploy')
APPS = {
    'personal-apps-home': {'port':8880, 'services':['personal-apps-home'], 'database':None},
    'rookcursion': {'port':8890, 'services':['rookcursion'], 'database':'chess.sqlite'},
    'fivek': {'port':8765, 'services':['fivek','fivek-proxy'], 'database':'data/training.sqlite3'},
}


def run(args, **kwargs):
    return subprocess.run(args, check=True, **kwargs)


def build_user(name, args, **kwargs):
    home = STATE/'build'/name
    return run(['runuser','-u',name+'-build','--','env',f'HOME={home}',*map(str,args)], **kwargs)


def event(name, message):
    print(json.dumps({'app':name,'message':message}), flush=True)


def read_state(name):
    path = STATE/(name+'.json')
    return json.loads(path.read_text()) if path.exists() else {}


def write_state(name, state):
    path = STATE/(name+'.json')
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(state, indent=2))
    os.replace(temp,path)


def status(name):
    if name=='personal-apps-home':
        owner=next(line.partition('=')[2].strip().strip(chr(34)).strip(chr(39)) for line in Path('/etc/personal-apps-home/app.env').read_text().splitlines() if line.startswith('TAILSCALE_OWNER='))
        request=urllib.request.Request('http://127.0.0.1:8880/',headers={'Tailscale-User-Login':owner})
        with urllib.request.urlopen(request,timeout=1) as response:
            if b'Your apps.' not in response.read():
                raise ValueError('Homepage missing')
        return {}
    with urllib.request.urlopen(f'http://127.0.0.1:{APPS[name]["port"]}/api/status',timeout=1) as response:
        return json.load(response)


def busy(name):
    try:
        value = status(name)
        return bool(value.get('busy') or (value.get('activeJob') or {}).get('status')=='running')
    except (OSError,ValueError):
        return False


def healthy(name):
    for _ in range(20):
        try:
            status(name)
            for unit in APPS[name]['services']:
                run(['systemctl','is-active','--quiet',unit])
            # Exercise the owner bridge, which must reject an anonymous request.
            import http.client
            port = {'fivek':8876,'rookcursion':8891,'personal-apps-home':8880}[name]
            conn=http.client.HTTPConnection('127.0.0.1',port,timeout=1)
            try:
                conn.request('GET','/');response=conn.getresponse();response.read()
                if response.status!=403:
                    raise ValueError('Owner isolation check failed')
            finally:
                conn.close()
            return True
        except (OSError,ValueError,subprocess.CalledProcessError):
            time.sleep(0.25)
    return False


def atomic_link(target, link):
    temporary=link.with_name(link.name+'.next')
    if temporary.is_symlink():
        temporary.unlink()
    temporary.symlink_to(target,target_is_directory=True)
    os.replace(temporary,link)


def restart(name):
    run(['systemctl','start',*APPS[name]['services']])


def legacy_layout(name):
    root=Path('/opt')/name
    current=root/'app'
    if not current.exists():
        return None
    if not current.is_symlink():
        legacy=root/('legacy-'+str(int(time.time())))
        current.rename(legacy)
        if name=='fivek':
            (root/'venv').rename(legacy/'.venv')
            (root/'venv').symlink_to(root/'app'/'.venv',target_is_directory=True)
        atomic_link(legacy,current)
    return current.resolve()


def database_backup(name):
    if not APPS[name]['database']:
        return
    source=Path('/var/lib')/name/APPS[name]['database']
    if not source.exists():
        return
    directory=Path('/var/backups/personal-apps')/name
    directory.mkdir(parents=True,exist_ok=True,mode=0o700)
    destination=directory/(str(time.time_ns())+'.sqlite')
    with sqlite3.connect(source.as_uri()+'?mode=ro',uri=True) as src, sqlite3.connect(destination) as dst:
        src.backup(dst)
        if dst.execute('PRAGMA quick_check').fetchone()[0]!='ok':
            raise RuntimeError('Pre-deployment database backup failed')
    destination.chmod(0o600)
    for stale in sorted(directory.glob('*.sqlite'))[:-5]:
        stale.unlink()


def activate(name, release):
    current=Path('/opt')/name/'app'
    timer_active=False
    if name=='fivek':
        timer_active=subprocess.run(['systemctl','is-active','--quiet','fivek-refresh.timer']).returncode==0
        run(['systemctl','stop','fivek-refresh.timer','fivek-proxy.service'])
    try:
        scheduled = name=='fivek' and subprocess.run(['systemctl','is-active','--quiet','fivek-refresh.service']).returncode==0
        if busy(name) or scheduled:
            event(name,'Deferred until active import or analysis finishes')
            return False
        run(['systemctl','stop',*APPS[name]['services']])
        previous=legacy_layout(name)
        try:
            database_backup(name)
            atomic_link(release,current)
            restart(name)
            if not healthy(name):
                raise RuntimeError('New release failed health checks')
        except Exception:
            run(['systemctl','stop',*APPS[name]['services']])
            if previous is None:
                current.unlink(missing_ok=True)
                raise
            atomic_link(previous,current)
            restart(name)
            if not healthy(name):
                raise RuntimeError('Rollback also failed health checks; administrator action required')
            event(name,'Restored previous release')
            raise
        return True
    finally:
        if name=='fivek':
            run(['systemctl','start','fivek-proxy.service'])
            if timer_active:
                run(['systemctl','start','fivek-refresh.timer'])


def release_build(name, sha, repo):
    root=Path('/opt')/name/'releases'
    root.mkdir(exist_ok=True)
    release=root/sha
    marker=release/'.build-passed'
    if marker.exists():
        return release
    if release.exists():
        remove_worktree(name,repo,release)
    account=pwd.getpwnam(name+'-build')
    release.mkdir(mode=0o755)
    os.chown(release,account.pw_uid,account.pw_gid)
    build_user(name,['git','-C',repo,'worktree','add','--detach',release,sha])
    if (release/'deploy/automation/test_auto_deploy.py').exists():
        build_user(name,['python3','deploy/automation/test_auto_deploy.py','-q'],cwd=release)
    if name=='personal-apps-home':
        build_user(name,['python3','-m','unittest','discover','-q'],cwd=release)
    elif name=='rookcursion':
        build_user(name,['npm','ci','--no-audit','--no-fund'],cwd=release)
        build_user(name,['npm','test'],cwd=release)
        build_user(name,['npm','run','build'],cwd=release)
    else:
        venv=release/'.venv'
        build_user(name,['python3','-m','venv',venv],cwd=release)
        build_user(name,[venv/'bin/pip','install','.'],cwd=release)
        build_user(name,['env','PYTHONPATH=src',venv/'bin/python','-m','unittest','discover','-s','tests','-q'],cwd=release)
        build_user(name,['env','PYTHONPATH=src',venv/'bin/python','deploy/test_private_proxy.py','-q'],cwd=release)
    marker.touch()
    return release


def remove_worktree(name, repo, release):
    root=Path('/opt')/name/'releases'
    if release.parent!=root or not re.fullmatch('[a-f0-9]{40}',release.name):
        raise ValueError('Unsafe release cleanup path')
    if release.resolve()==(Path('/opt')/name/'app').resolve():
        raise ValueError('Cannot remove the active release')
    run(['git','-c',f'safe.directory={repo}','-C',str(repo),'worktree','remove','--force',str(release)])


def prune(name, repo):
    root=Path('/opt')/name/'releases'
    current=(Path('/opt')/name/'app').resolve()
    releases=sorted((p for p in root.iterdir() if re.fullmatch('[a-f0-9]{40}',p.name)),key=lambda p:p.stat().st_mtime,reverse=True)
    # Keep current and the three newest completed/building releases.
    for old in releases[3:]:
        if old!=current:
            remove_worktree(name,repo,old)


def deploy(name, retry=False, requested_sha=None):
    state=read_state(name)
    try:
        repo=STATE/'build'/name/'repo.git'
        if not repo.exists():
            build_user(name,['git','init','--bare',repo])
            build_user(name,['git','-C',repo,'remote','add','origin',f'https://github.com/gfa789/{name}.git'])
        build_user(name,['git','-C',repo,'fetch','--no-tags','origin','+refs/heads/main:refs/remotes/origin/main'])
        sha=build_user(name,['git','-C',repo,'rev-parse','refs/remotes/origin/main'],capture_output=True,text=True).stdout.strip()
        if not re.fullmatch('[a-f0-9]{40}',sha):
            raise ValueError('Invalid Git revision')
        if requested_sha and requested_sha!=sha:
            raise ValueError('Requested revision is no longer the main branch head; deploy the latest workflow')
        if state.get('deployed_sha')==sha:
            return
        if state.get('failed_sha')==sha and time.time()-state.get('failed_at',0)<900 and not retry:
            return False
        if busy(name):
            event(name,'Deployment waits for current work')
            return False
        event(name,'Building '+sha)
        release=release_build(name,sha,repo)
        if not activate(name,release):
            return False
        state={'deployed_sha':sha,'deployed_at':time.time(),'branch':'main'}
        write_state(name,state)
        event(name,'Deployed '+sha)
        try:
            prune(name,repo)
        except Exception as error:
            event(name,'Release cleanup needs attention: '+str(error))
    except Exception as error:
        state.update(failed_sha=locals().get('sha'),failed_at=time.time(),error=str(error))
        write_state(name,state)
        event(name,'Deployment failed: '+str(error))
        raise


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('app',choices=[*APPS,'all'])
    parser.add_argument('--retry',action='store_true')
    parser.add_argument('--sha')
    parser.add_argument('--wait',action='store_true')
    args=parser.parse_args()
    if args.sha and (args.app=='all' or not re.fullmatch('[a-f0-9]{40}',args.sha)):
        parser.error('--sha requires one app and a full Git revision')
    STATE.mkdir(exist_ok=True,mode=0o755)
    with (STATE/'deploy.lock').open('w') as lock:
        deadline=time.monotonic()+720
        while True:
            try:
                fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if not args.wait:
                    sys.exit(1)
                if time.monotonic()>deadline:
                    raise TimeoutError('Timed out waiting for another deployment')
                time.sleep(5)
        failed=False
        for name in APPS if args.app=='all' else [args.app]:
            try:
                while deploy(name,args.retry or bool(args.sha),args.sha) is False:
                    if not args.wait:
                        raise RuntimeError('Deployment deferred; retry when idle')
                    if time.monotonic()>deadline:
                        raise TimeoutError('Application stayed busy; retry workflow later')
                    time.sleep(10)
            except Exception:
                failed=True
        if failed:
            sys.exit(1)


if __name__=='__main__':
    main()
