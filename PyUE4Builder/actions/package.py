#!/usr/bin/env python

from actions.action import Action
from utility.common import launch, print_action, print_warning
import click
import shutil
import os
import stat

__author__ = "Ryan Sheffer"
__copyright__ = "Copyright 2018, Ryan Sheffer Open Source"
__credits__ = ["Ryan Sheffer", "VREAL"]


class Package(Action):
    """
    Package action.
    This action is used to build, cook and package a project.
    """

    # Other relative to project paths
    build_blacklist_dir = 'Build\\Win64'

    # Constants
    blacklist_file_name = 'PakBlacklist-{0}.txt'  # Param: Build name

    def __init__(self, config, **kwargs):
        super().__init__(config, **kwargs)

        self.pak_assets = kwargs['pak_assets'] if 'pak_assets' in kwargs else True
        self.nativize_assets = kwargs['nativize_assets'] if 'nativize_assets' in kwargs else True
        self.compressed_assets = kwargs['compressed_assets'] if 'compressed_assets' in kwargs else True
        self.no_debug_info = kwargs['no_debug_info'] if 'no_debug_info' in kwargs else False
        self.full_rebuild = kwargs['full_rebuild'] if 'full_rebuild' in kwargs else False
        self.no_editor_content = kwargs['no_editor_content'] if 'no_editor_content' in kwargs else False
        self.ignore_cook_errors = kwargs['ignore_cook_errors'] if 'ignore_cook_errors' in kwargs else False
        self.use_debug_editor_cmd = kwargs['use_debug_editor_cmd'] if 'use_debug_editor_cmd' in kwargs else False
        self.build_type = kwargs['build_type'] if 'build_type' in kwargs else ''
        self.maps = kwargs['maps'] if 'maps' in kwargs else []

        # Control the pipeline
        self.build = kwargs['build'] if 'build' in kwargs else True
        self.cook = kwargs['cook'] if 'cook' in kwargs else True
        self.package = kwargs['package'] if 'package' in kwargs else True
        self.stage = kwargs['stage'] if 'stage' in kwargs else True
        self.archive = kwargs['archive'] if 'archive' in kwargs else True

        # Argument to specify a content blacklist to use with this packaging.
        self.content_black_list = kwargs['content_black_list'] if 'content_black_list' in kwargs else ''

    def verify(self):
        if not self.config.check_environment():
            return 'Environment is not ready for building or packaging!'

        valid_build_types = ['standalone', 'client', 'server']
        if self.build_type not in valid_build_types:
            print_warning('Unrecognized build type ({}) for package. Defaulting to "standalone".\n'
                          'Valid types={}'.format(self.build_type, valid_build_types))
            self.build_type = 'standalone'

        return ''

    def run(self):
        self.error = self.verify()
        if self.error != '':
            return False

        click.secho('Building for client version {}'.format(self.config.version_str))

        if self.config.clean:
            # Kill the build directories
            def on_rm_error(func, path, exc_info):
                # path contains the path of the file that couldn't be removed
                # let's just assume that it's read-only and unlink it.
                del func  # Unused
                if exc_info[0] is not FileNotFoundError:
                    os.chmod(path, stat.S_IWRITE)
                    os.unlink(path)

            if self.build_type == 'client':
                shutil.rmtree(os.path.join(self.config.builds_path, 'WindowsClient'), onerror=on_rm_error)
            elif self.build_type == 'server':
                shutil.rmtree(os.path.join(self.config.builds_path, 'WindowsServer'), onerror=on_rm_error)
            else:
                shutil.rmtree(os.path.join(self.config.builds_path, 'WindowsNoEditor'), onerror=on_rm_error)

        cap_build_name = self.config.uproject_name.title()
        print_action('Building {} Build'.format(cap_build_name))

        build_blacklist_file_path = os.path.join(self.config.uproject_dir_path,
                                                 self.build_blacklist_dir,
                                                 self.blacklist_file_name.format(self.config.configuration))
        if self.content_black_list != '':
            print_action('Setting up content blacklist for configuration {}'.format(self.config.configuration))
            if os.path.isfile(build_blacklist_file_path):
                os.unlink(build_blacklist_file_path)
            os.makedirs(os.path.join(self.config.uproject_dir_path, self.build_blacklist_dir), exist_ok=True)
            shutil.copyfile(os.path.join(self.config.uproject_dir_path, self.content_black_list),
                            build_blacklist_file_path)

        cmd_args = ['-ScriptsForProject={}'.format(self.config.uproject_file_path),
                    'BuildCookRun', '-nocompileeditor', '-NoHotReload', '-nop4',
                    '-project={}'.format(self.config.uproject_file_path),
                    '-archivedirectory={}'.format(self.config.builds_path),
                    '-clientconfig={}'.format(self.config.configuration),
                    '-serverconfig={}'.format(self.config.configuration),
                    '-ue4exe={}'.format('UE4Editor-Win64-Debug-Cmd.exe' if self.use_debug_editor_cmd else
                                        'UE4Editor-Cmd.exe'),
                    '-prereqs', '-targetplatform=Win64', '-platform=Win64',
                    '-servertargetplatform=Win64', '-serverplatform=Win64',
                    '-CrashReporter', '-utf8output']

        if self.build:
            cmd_args.append('-build')
        if self.cook:
            cmd_args.append('-cook')
        if self.package:
            cmd_args.append('-package')
        if self.stage:
            cmd_args.append('-stage')
        if self.archive:
            cmd_args.append('-archive')

        if self.build_type == 'client':
            cmd_args.append('-client')
        elif self.build_type == 'server':
            cmd_args.extend(['-server', '-noclient'])

        if self.nativize_assets:
            cmd_args.append('-nativizeAssets')
        if self.pak_assets and self.stage:
            cmd_args.append('-pak')
        if self.compressed_assets:
            cmd_args.append('-compressed')
        if self.no_debug_info:
            cmd_args.append('-nodebuginfo')
        if self.no_editor_content:
            cmd_args.append('-SkipCookingEditorContent')
        if self.ignore_cook_errors:
            cmd_args.append('-IgnoreCookErrors')

        if len(self.maps) > 0:
            cmd_args.append('-map={}'.format('+'.join(self.maps)))

        if self.config.clean or self.full_rebuild:
            cmd_args.append('-clean')
        else:
            cmd_args.extend(['-iterate', '-iterativecooking'])

        cmd_args.append('-compile')

        print_action('Building, Cooking, and Packaging {} Build'.format(cap_build_name))
        if launch(self.config.UE4RunUATBatPath, cmd_args) != 0:
            self.error = 'Unable to build {}!'.format(self.config.uproject_name)
            return False

        # Don't leave blacklist around
        if os.path.isfile(build_blacklist_file_path):
            os.unlink(build_blacklist_file_path)

        return True
