"""Deployment failure must leave the previous release usable."""
import importlib.util
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch
import unittest

spec=importlib.util.spec_from_file_location('auto_deploy',Path(__file__).with_name('auto-deploy.py'))
deploy=importlib.util.module_from_spec(spec)
spec.loader.exec_module(deploy)


class DeploymentTests(unittest.TestCase):
    def test_atomic_release_switch(self):
        with TemporaryDirectory() as directory:
            root=Path(directory)
            old=root/'old';old.mkdir()
            new=root/'new';new.mkdir()
            current=root/'app';current.symlink_to(old)
            deploy.atomic_link(new,current)
            self.assertEqual(current.resolve(),new)
            self.assertTrue(old.is_dir())

    def test_failed_health_check_rolls_back(self):
        with TemporaryDirectory() as directory:
            root=Path(directory)
            app=root/'rookcursion';app.mkdir()
            old=app/'old';old.mkdir()
            new=app/'new';new.mkdir()
            (app/'app').symlink_to(old)
            def paths(value):
                return root if value=='/opt' else Path(value)
            with patch.object(deploy,'Path',side_effect=paths), patch.object(deploy,'run'), \
                 patch.object(deploy,'busy',return_value=False), \
                 patch.object(deploy,'legacy_layout',return_value=old), \
                 patch.object(deploy,'database_backup'), patch.object(deploy,'restart'), \
                 patch.object(deploy,'healthy',side_effect=[False,True]):
                with self.assertRaisesRegex(RuntimeError,'New release failed'):
                    deploy.activate('rookcursion',new)
            self.assertEqual((app/'app').resolve(),old)

    def test_busy_app_is_not_stopped(self):
        with patch.object(deploy,'busy',return_value=True), patch.object(deploy,'run') as run:
            self.assertFalse(deploy.activate('rookcursion',Path('/unused')))
            run.assert_not_called()

    def test_build_failure_does_not_activate_or_lose_previous_revision(self):
        old='a'*40;new='b'*40
        state={'deployed_sha':old}
        result=SimpleNamespace(stdout=new,returncode=0)
        with patch.object(Path,'exists',return_value=False), \
             patch.object(deploy,'read_state',return_value=state), \
             patch.object(deploy,'build_user',return_value=result), \
             patch.object(deploy,'busy',return_value=False), \
             patch.object(deploy,'release_build',side_effect=RuntimeError('Tests failed')), \
             patch.object(deploy,'write_state') as save, patch.object(deploy,'activate') as activate:
            with self.assertRaisesRegex(RuntimeError,'Tests failed'):
                deploy.deploy('rookcursion')
            activate.assert_not_called()
            saved=save.call_args.args[1]
            self.assertEqual(saved['deployed_sha'],old)
            self.assertEqual(saved['failed_sha'],new)


if __name__=='__main__':
    unittest.main()
