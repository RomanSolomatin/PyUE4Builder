#!/usr/bin/env python

import sys
import os
import click
import json
import importlib
from copy import deepcopy
from build_meta import BuildMeta
from config import ProjectConfig, project_configurations, platform_types
from utility.common import launch, print_title, print_action, error_exit, print_error, \
    get_visual_studio_version, register_project_engine, print_warning
from actions.build import Build
from actions.package import Package
from actions.git import Git

__author__ = "Ryan Sheffer"
__copyright__ = "Copyright 2018, Ryan Sheffer Open Source"
__credits__ = ["Ryan Sheffer", "VREAL"]


@click.command()
@click.option('--clean/--no-clean',
              default=False,
              show_default=True,
              help='Causes all actions to consider cleaning up their workspaces before executing their action.')
@click.option('--platform', '-p',
              type=click.Choice(platform_types),
              default='Win64',
              show_default=True,
              help="Specifies the platform to build for. Defaults to Win64.")
@click.option('--build', '-b',
              default='',
              show_default=True,
              help="If set, specifies build steps to run and nothing more. Basic engine tools will not be built.")
@click.option('--buildtype', '-t',
              type=click.STRING,
              default='Editor',
              show_default=True,
              help="Which type of build are you trying to create? Editor OR Package?")
@click.option('--configuration', '-c',
              type=click.Choice(project_configurations),
              default='Development',
              show_default=True,
              help="Build configuration, e.g. Shipping")
@click.option('--script', '-s',
              type=click.STRING,
              required=True,
              help='The Project Script which defines the projects paths, build steps, and extra information.')
@click.option('--engine', '-e',
              type=click.STRING,
              default='',
              help='The desired engine path, absolute or relative. Blank will try to find the engine for you.')
def build_script(engine, script, configuration, buildtype, build, platform, clean):
    """
    The Main call for build script execution.
    :param engine: The desired engine path, absolute or relative.
    :param script: The Project Script which defines the projects paths, build steps, and extra information.
    :param configuration: Build configuration, e.g. Shipping
    :param buildtype: Which type of build are you trying to create? Editor OR Package?
    :param build: Which build steps to execute?
    :param platform: Which platform to build for?
    :param clean: Causes all actions to consider cleaning up their workspaces before executing their action.
    """
    # Fixup for old build type 'Game'.
    if buildtype == 'Game':
        buildtype = 'Editor'

    # Ensure Visual Studio is installed
    if get_visual_studio_version() not in [2015, 2017]:
        print_error('Cannot run build, valid visual studio install not found!')
        return False

    if not os.path.isfile(script):
        error_exit('No build script defined! Use the -s arg')

    with open(script, 'r') as fp:
        try:
            script_json = json.load(fp)
        except Exception as jsonError:
            error_exit('Build Script Syntax Error:\n{}'.format(jsonError))
            return

    config = ProjectConfig(configuration, platform, False, clean)
    if not config.load_configuration(script_json, engine, False):
        error_exit('Failed to load configuration. See errors above.')

    print_title('Unreal Project Builder')

    build_meta = BuildMeta('project_build_meta')
    if "meta" in config.script:
        build_meta.insert_meta(**config.script["meta"])

    # Ensure the engine exists and we can build
    ensure_engine(config, engine)
    click.secho('\nProject File Path: {}\nEngine Path: {}'.format(config.uproject_dir_path, config.UE4EnginePath))

    # Ensure the unreal header tool exists. It is important for all Unreal projects
    if not os.path.isfile(os.path.join(config.UE4EnginePath, 'Engine\\Binaries\\Win64\\UnrealHeaderTool.exe')):
        b = Build(config, build_name='UnrealHeaderTool')
        if not b.run():
            error_exit(b.error)

    # Build required engine tools
    clean_revert = config.clean
    if buildtype == "Package":
        config.clean = False  # Don't clean if packaging, waste of time
    for tool_name in config.build_engine_tools:
        b = Build(config, build_name=tool_name)
        if not b.run():
            error_exit(b.error)
    config.clean = clean_revert

    # If a specific set of steps if being requested, only build those
    if build != '':
        run_build_steps(config, build_meta, build, True)
    else:
        # Ensure engine is built
        if not config.editor_running:
            clean_revert = config.clean
            if buildtype == "Package":
                config.clean = False  # Don't clean if packaging, waste of time
            b = Build(config, build_name='UE4Editor')
            if not b.run():
                error_exit(b.error)
            config.clean = clean_revert
        else:
            print_warning('Skipping engine build because engine is running!')

        run_build_steps(config, build_meta, 'pre_build_steps')

        if buildtype == "Editor":
            if config.editor_running:
                print_warning('Cannot build the Editor while the editor is running!')
                click.pause()
                sys.exit(1)

            if 'game_editor_steps' in config.script:
                run_build_steps(config, build_meta, 'game_editor_steps')
            elif 'editor_steps' in config.script:
                run_build_steps(config, build_meta, 'editor_steps')
            else:
                b = Build(config, build_name='{}Editor'.format(config.uproject_name))
                if not b.run():
                    error_exit(b.error)

        elif buildtype == "Package":
            if 'package_steps' in config.script:
                run_build_steps(config, build_meta, 'package_steps')
            else:
                package = Package(config)
                if not package.run():
                    error_exit(package.error)

        run_build_steps(config, build_meta, 'post_build_steps')

    build_meta.save_meta()
    print_action('SUCCESS!')
    click.pause()


def ensure_engine(config, engine_override):
    """
    Pre-work step of ensuring we have a valid engine and enough components exist to do work
    :param config: The project configuration (may not point to a valid engine yet)
    :param engine_override: The desired engine directory path to use
    """
    can_pull_engine = config.git_repo != '' and config.git_proj_branch != ''

    if config.UE4EnginePath == '':
        if not can_pull_engine and engine_override == '':
            error_exit('Static engine placement required for non-git pulled engine. '
                       'You can specify a path using the -e param, or specify git configuration.')

        if engine_override != '':
            config.setup_engine_paths(os.path.abspath(engine_override))
        else:
            result = click.confirm('Would you like to specify the location of the engine install?', default=False)
            if result:
                result = click.prompt('Where would you like to install the engine?')
                if not os.path.exists(result):
                    try:
                        os.makedirs(result)
                    except Exception:
                        error_exit('Unable to create engine directory! Tried @ {}'.format(result))
                config.setup_engine_paths(result)
            else:
                # Find an ideal location to put the engine
                if len(config.engine_path_name) == 0:
                    # Put the engine one directory down from the uproject
                    engine_path = os.path.abspath(os.path.join(config.uproject_dir_path,
                                                               '..\\UnrealEngine_{}'.format(config.uproject_name)))
                else:
                    if os.path.isabs(config.engine_path_name):
                        engine_path = config.engine_path_name
                    else:
                        engine_path = os.path.normpath(os.path.join(config.uproject_dir_path, config.engine_path_name))

                if not os.path.exists(engine_path):
                    try:
                        os.makedirs(engine_path)
                    except Exception:
                        error_exit('Unable to create engine directory! Tried @ {}'.format(engine_path))
                config.setup_engine_paths(engine_path)
    elif config.UE4EnginePath != engine_override and engine_override != '':
        error_exit('Specific engine path requested, but engine path for this project already exists?')

    # Before doing anything, make sure we have all build dependencies ready
    if can_pull_engine:
        git_action = Git(config)
        git_action.branch_name = config.git_proj_branch
        git_action.repo_name = config.git_repo
        git_action.output_folder = config.UE4EnginePath
        git_action.disable_strict_hostkey_check = True
        git_action.force_repull = False
        if not git_action.run():
            error_exit(git_action.error)

    if not config.setup_engine_paths(engine_override):
        error_exit('Could not setup valid engine paths!')

    # Register the engine (might do nothing if already registered)
    # If no key name, this is an un-keyed static engine.
    if config.UE4EngineKeyName != '':
        register_project_engine(config, False)

    if not config.editor_running:
        print_action('Checking engine dependencies up-to-date')

        def add_dep_exclude(path_name, args):
            args.append('-exclude={}'.format(path_name))

        cmd_args = []
        if config.exclude_samples:
            for sample_pack in ['FeaturePacks', 'Samples', 'Templates']:
                add_dep_exclude(sample_pack, cmd_args)
        for extra_exclude in config.extra_dependency_excludes:
            add_dep_exclude(extra_exclude, cmd_args)

        if launch(config.UE4GitDependenciesPath, cmd_args) != 0:
            error_exit('Engine dependencies Failed to Sync!')

        if not os.path.isfile(config.UE4UBTPath):
            # The unreal build tool does not exist, we need to build it first
            # We use the generate project files batch script because it ensures the build tool exists,
            # and builds it if not.
            print_action("Build tool doesn't exist yet, generating project and building...")
            if launch(config.UE4GenProjFilesPath, ['-2017'] if get_visual_studio_version() == 2017 else []) != 0:
                error_exit('Failed to build UnrealBuildTool.exe!')


def run_build_steps(config: ProjectConfig, build_meta: BuildMeta, steps_name, complain_missing_step: bool=False):
    """
    Runs the build steps defined in a build script.
    A valid script json must be loaded in config!
    :param config: The configuration for this project
    :param build_meta: Build meta which might contain requirements for a build step
    :param steps_name: The steps to perform, defined in the script
    :param complain_missing_step: True if you would like the steps runner to complain about this step not existing.
    """
    if config.script is None:
        print_warning('Script json not loaded for run_build_steps!')
        return

    if steps_name in config.script:
        steps = config.script[steps_name]
        for step in steps:
            if "enabled" in step and step["enabled"] is False:
                continue

            print_action('Performing Undescribed step' if 'desc' not in step else step['desc'])

            # Get the step class
            step_module = importlib.import_module(step['action']['module'])
            class_name = step['action']['module'].split('.')[-1]
            action_class = getattr(step_module, class_name.title(), None)
            if action_class is None:
                print_warning('action class ({}) could not be found!'.format(class_name.title()))
                continue

            # Create kwargs of requested arguments
            kwargs = {}
            if 'meta' in step['action']:
                kwargs.update(build_meta.collect_meta(step['action']['meta']))

            if 'args' in step['action']:
                kwargs.update(step['action']['args'])

            # Run the action
            # We deep copy the configuration so it cannot be tampered with from inside the action.
            b = action_class(deepcopy(config), **kwargs)
            verify_error = b.verify()
            if verify_error != '':
                if "allow_failure" in step and step["allow_failure"] is True:
                    print_warning(verify_error)
                    print_warning('Verification of this action failed. Skipping because of allow_failure flag.')
                    return
                else:
                    error_exit(verify_error)

            if not b.run():
                if "allow_failure" in step and step["allow_failure"] is True:
                    print_warning(b.error)
                    print_warning('Running of this action failed. Skipping because of allow_failure flag.')
                    return
                else:
                    error_exit(b.error)

            # Do meta updates
            if 'meta_updates' in step['action']:
                for k, v in step['action']['meta_updates'].items():
                    meta_item = getattr(b, v, None)
                    if meta_item is not None:
                        setattr(build_meta, k, meta_item)
                build_meta.save_meta()
    elif complain_missing_step:
        print_warning('Could not find build step ({}) for run_build_steps!'.format(steps_name))

if __name__ == "__main__":
    try:
        build_script()
    except Exception as e:
        error_exit('{}'.format(e))
