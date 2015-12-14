import logging
from json import dump, load
from os import chdir, getcwd, listdir, mkdir
from os.path import exists, join
from shutil import copyfile, rmtree
from tempfile import mkdtemp

import requests
from platformio.util import get_boards

from git import GitCommandError, Repo
from platformio_api import config
from platformio_api.exception import RemoteBranchNotFound
from platformio_api.util import get_libexample_dir

logger = logging.getLogger(__name__)

VERSION_FILENAME = "_version.json"
TRAVIS_FILENAME = ".travis.yml"
COMMIT_MESSAGE = "Update library to version {library_version}"

TRAVIS_CONFIG_TEMPLATE = """\
language: python
python:
    - "2.7"

sudo: false

# Cache PlatformIO packages using Travis CI container-based infrastructure
cache:
    directories:
        - "~/.platformio"

env:
{envs}

install:
    - pip install -U platformio

script:
    - platformio --force lib install --version={library_version} {library_id}
    - platformio --force ci {boards}
"""

BOARDS = {
    "atmelavr": ["uno", "leonardo", "megaatmega2560"],
    "teensy": ["teensy20", "teensy31"],
}


def shallow_clone_branch(branch, to, url=config["LIBBUILD_REPO_URI"]):
    """Performs shallow clone of desired branch.

    Resulting clone will have last commit of specified branch only.

    :param branch: a name of the branch to clone.
    :param to: path to the directory where repo should be placed.
    :param url: repo origin.
    """
    try:
        return Repo.clone_from(url, to, **{
            "branch": branch,
            "single-branch": True,
            "depth": 1,
        })
    except GitCommandError as exc:
        expected_message = \
            "fatal: Remote branch %s not found in upstream origin" % branch
        if exc.stderr and exc.stderr.split("\n")[-2] == expected_message:
            raise RemoteBranchNotFound(branch)
        raise


def get_all_boards_by_platforms(platforms):
    board_names = []
    for board, info in get_boards().items():
        if info.get("platform") in platforms:
            board_names.append(board)
    return board_names


def get_boards_by_platforms(platforms):
    board_names = []
    for p in platforms:
        board_names += BOARDS.get(p, [])
    return board_names


def think_of_name_later(lib_id, version, platforms, **kwargs):
    original_working_directory = getcwd()
    branch_name = "library-%s" % lib_id
    repo_directory = mkdtemp(prefix="pioapi-libbuild-%s-" % lib_id)
    try:
        try:
            logger.debug("Cloning branch {}".format(branch_name))
            repo = shallow_clone_branch(branch_name, repo_directory,
                                        config["LIBBUILD_REPO_URI"])
        except RemoteBranchNotFound:
            logger.debug("Branch not found. Making one.")
            repo = shallow_clone_branch("master", repo_directory,
                                        config["LIBBUILD_REPO_URI"])
            repo.git.checkout(branch_name, orphan=True)  # create empty branch
            # repo.git.branch(set_upstream_to="origin/" + branch_name)
        logger.info("Cloned into {}".format(repo_directory))

        # Other paths are relative to the root of the repository
        chdir(repo.working_tree_dir)

        # Ensure version has not already been
        version_filename = join(repo_directory, VERSION_FILENAME)
        if exists(version_filename):
            with open(version_filename, "r") as version_file:
                staged_version = load(version_file)
                if version == staged_version and not kwargs.get("force"):
                    logger.info("Staged version is equal to current version. "
                                 "Update not required.")
                    return
        with open(version_filename, "w") as version_file:
            dump(version, version_file, indent=2)

        examples_directory = "examples"

        # Clear examples directory
        if exists(examples_directory):
            rmtree(examples_directory)
        mkdir(examples_directory)

        # Copy examples
        example_files = []
        saved_examples_directory = get_libexample_dir(lib_id)
        for example_filename in listdir(saved_examples_directory):
            final_example_path = join(examples_directory, example_filename)
            copyfile(join(saved_examples_directory, example_filename),
                     final_example_path)
            example_files.append(final_example_path)

        # Write Travis-CI config
        boards = " ".join("--board=" + board_type
                          for board_type in get_boards_by_platforms(platforms))
        envs = "\n".join("    - PLATFORMIO_CI_SRC={}".format(path)
                         for path in example_files)
        with open(TRAVIS_FILENAME, "w") as travis_file:
            travis_file.write(TRAVIS_CONFIG_TEMPLATE.format(
                envs=envs, boards=boards,
                library_version=version["name"], library_id=lib_id,
            ))

        # Make commit
        repo.index.add(example_files + [VERSION_FILENAME, TRAVIS_FILENAME])
        repo.index.commit(COMMIT_MESSAGE.format(
            library_version=version["name"]))
        repo.remote("origin").push(branch_name, set_upstream=True)

    except Exception as exc:
        logger.exception(exc)

    finally:
        chdir(original_working_directory)
        rmtree(repo_directory)


def think_of_name_later_by_id(lib_id):
    info = requests.get("http://api.platformio.org/lib/info/" + str(lib_id))\
        .json()
    return think_of_name_later(lib_id, info["version"], info["platforms"])


if __name__ == '__main__':
    think_of_name_later_by_id(75)
