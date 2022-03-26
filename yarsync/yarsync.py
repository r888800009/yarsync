# Yet Another Rsync

import argparse
import collections
import configparser
import datetime
# for user name
import getpass
import io
import json
import os
import re
# for host name
# import platform
import socket
import subprocess
import sys
import time


# private, because I see no reason to document this
def _is_commit(file_name):
    """A file name is a commit
    if its name can be converted to int.
    """
    try:
        int(file_name)
    except (TypeError, ValueError):
        return False
    return True


# copied from https://github.com/DiffSK/configobj/issues/144#issuecomment-347019778
# with some modifications.
# Another, and maybe a better option, would be
# config = ConfigParser(os.environ)
# config.read('config.ini')
# where it's probably not necessary to add all the ENV to the config,
# but only those variables that occur in the config file.
def _substitute_env(content):
    """Reads filename, substitutes environment variables and returns a file-like
     object of the result.

    Substitution maps text like "$FOO" for the environment variable "FOO".
    """

    def lookup(match):
        """Replaces a match like $FOO with the env var FOO.
        """
        key = match.group(2)
        if key not in os.environ:
            # variables should be set for values that are used,
            # but not necessarily for all values.
            # raise OSError("variable {} unset".format(key))
            # unset variables return unchanged (with $)
            return match.group(1)
            # raise Exception("Config env var '{key}' not set".format(key))
        return os.environ.get(key)

    # todo: allow more sophisticated variables, like ${VAR}
    # (and that's all), should be an OR of this and 
    # r'(\${(\w+)})'), untested.
    # Not sure it's needed: why such complications to a config file?..
    pattern = re.compile(r'(\$(\w+))')
    replaced = pattern.sub(lookup, content)

    try:
        result = io.StringIO(replaced)
    except TypeError:  # Python2
        result = io.StringIO(unicode(replaced, "utf-8"))
    return result


def _mkhostpath(host, path):
    if host:
        return host + ":" + path
    # localhost
    return path


class YARsync():
    """Synchronize data. Provide configuration and wrap rsync calls."""

    def __init__(self, argv):
        """*argv* is the list of command line arguments."""

        parser = argparse.ArgumentParser(description='sync directories')
        subparsers = parser.add_subparsers(
            title='commands',
            dest='command_name',
            description='valid commands',
            help='additional help'
        )

        ###################################
        ## Initialize optional arguments ##
        ###################################
        # or ys_dir
        parser.add_argument('--config-dir', default="",
                            help="path to the configuration directory")
        parser.add_argument('--root-dir', default="",
                            help="path to the root of the working directory")

        parser.add_argument('-D', '--destname',
                            help="destination name used for logging")
        ## host = target = destination name,
        ## or source name during yarsync init.
        ## -H, because -h corresponds to --help
        # the algorithm is different depending on
        # whether it's a remote or a local repository.

        parser.add_argument(
            "-n", "--dry-run", action="store_true",
            default=False,
            help="print what would be transferred during a real run, "
                 "but don't make any changes"
        )
        # I think this should work with push, pull, and anything involving rsync
        # (but not init or log or status? Or maybe give a hint what's going on?)
        # Oh no, -n with log is number of commits.

        parser.add_argument("-q", "--quiet", action="count",
                            help="suppress normal output")

        ############################
        ## Initialize subcommands ##
        ############################
        # or sub-commands

        # commit #
        parser_commit = subparsers.add_parser("commit",
                                            help="commit changes")
        parser_commit.add_argument("-m", "--message", default="",
                                   help="commit message")
        parser_commit.set_defaults(func=self._commit)

        # init #
        parser_init = subparsers.add_parser("init",
                                            help="initialize a repository")
        parser_init.add_argument("reponame", nargs="?",
                                 help="name of the created repository")
        parser_init.set_defaults(func=self._init)

        # log #
        parser_log = subparsers.add_parser(
            "log", help="show commit logs"
        )
        parser_log.add_argument(
            "-n", "--max-count", metavar="<number>", type=int,
            default=-1,
            help="name of the created repository"
        )
        parser_log.add_argument("-r", "--reverse", action="store_true",
                                help="reverse the order of output")
        parser_log.set_defaults(func=self._log)

        # pull #
        parser_pull = subparsers.add_parser(
            "pull", help="update data from the source"
        )
        parser_pull.add_argument(
            "--new", action="store_true",
            help="don't remove files missing on the source here"
        )
        parser_pull.add_argument("source", nargs="?",
                                 help="source name or path")
        parser_pull.add_argument(
            "-n", "--dry-run", action="store_true",
            default=False,
            help="print what would be transferred during a real run, "
                 "but don't make any change"
        )
        parser_pull.set_defaults(func=self._pull_push)

        # push #
        parser_push = subparsers.add_parser(
            "push", help="update data on the destination"
        )
        parser_push.add_argument(
            "-n", "--dry-run", action="store_true",
            default=False,
            help="print what would be transferred during a real run, "
                 "but don't make any change"
        )
        parser_push.add_argument(
            "--new", action="store_true",
            help="don't remove files missing here on the destination"
        )
        parser_push.add_argument("destination", nargs="?",
                                 help="destination name or path")
        parser_push.set_defaults(func=self._pull_push)

        ## remote ##
        parser_remote = subparsers.add_parser(
            "remote", help="manage remote repositories"
        )
        parser_remote.add_argument(
            "-v", "--verbose", action="count",
            help="show remote paths. Insert after 'remote'"
        )
        subparsers_remote = parser_remote.add_subparsers(dest="command")

        # parse_intermixed_args is missing in Python 2,
        # that's why we allow -v flag only after 'remote'.
        #
        # remote_parent_parser = argparse.ArgumentParser(add_help=False)
        # remote_parent_parser.add_argument(
        #     "-v", "--verbose", action="count",
        #     help="show remote paths. Insert after a remote command"
        # )
        ## add
        remote_add = subparsers_remote.add_parser("add")
        ## rm
        remote_rm = subparsers_remote.add_parser("rm")
        # show, default
        remote_show = subparsers_remote.add_parser(
            "show"
            # "show", parents=[remote_parent_parser]
        )
        remote_show.set_defaults(func=self._remote_show)

        # it seems a parser doesn't know its subparsers,
        # no function is given in docs
        for subparser in [remote_add, remote_rm]:
            subparser.set_defaults(func=self._remote)

        # status
        parser_status = subparsers.add_parser(
            "status", help="print updates since last commit"
        )
        parser_status.set_defaults(func=self._status)

        # other useful commands (to be implemented): diff (show difference between commits)
        # clone? So that 1) can be bidirectional, to and from (unlike for git)
        #                2) know about hardlinks
        #                3) know target name
        # log <commit_number>

        if len(argv) > 1:  # 0th argument is always present
            args = parser.parse_args(argv[1:])
        else:
            # default is print help
            args = parser.parse_args(["--help"])

        ########################
        ## Init configuration ##
        ########################

        # basename, because ipython may print full path
        self.NAME = os.path.basename(argv[0])  # "yarsync"
        # todo: this file should contain repository name.
        # I think that would be better to move this to config.
        self.REPOFILENAME = "repository.txt"
        # directory with metadata
        CONFIGDIRNAME = ".ys"

        root_dir = os.path.expanduser(args.root_dir)
        config_dir = os.path.expanduser(args.config_dir)
        if not root_dir and not config_dir:
            if args.command_name == 'init':
                root_dir = "."
                config_dir = CONFIGDIRNAME
            else:
                # search the current directory and its parents
                try:
                    root_dir = self._get_root_directory(CONFIGDIRNAME)
                except OSError as err:
                    # config dir not found.
                    raise err
                config_dir = os.path.join(root_dir, CONFIGDIRNAME)
        else:
            # at least one of them is provided.
            # check that they are both provided
            if not root_dir or not config_dir:
                parser.error(
                    "both --config-dir and --root-dir must be provided"
                )
        self.root_dir = root_dir
        self.config_dir = config_dir

        # directory creation mode can be set from:
        # - command line argument
        # - global configuration
        # - mode of the sync-ed directory (may be best).
        self.DIRMODE = 0o755

        # CONFIG = "config.ini"
        self.CONFIGFILE = os.path.join(self.config_dir, "config.ini")
        self.COMMITDIR = os.path.join(self.config_dir, "commits")
        self.DATEFMT = "%a, %d %b %Y %H:%M:%S %Z"
        self.LOGDIR = os.path.join(self.config_dir, "logs")
        # REMOTESDIR = os.path.join(self.config_dir, "remotes")
        self.RSYNCFILTER = os.path.join(self.config_dir, "rsync-filter")
        # this could be a useful configuration, for example
        # when mounting a local repo and making a commit.
        self.REPOFILE = os.path.join(self.config_dir, self.REPOFILENAME)
        # stores last synchronized commit
        self.SYNCFILENAME = os.path.join(self.config_dir, "sync.txt")

        self.DEBUG = True

        if args.command_name not in ['init', 'status', 'log']:
            if not os.path.exists(self.CONFIGFILE):
                self._print_error(
                    "fatal: no {} configuration {} found".
                    format(self.NAME, self.CONFIGFILE)
                )
                raise OSError(
                    "{} not found".format(self.CONFIGFILE)
                )
            self._read_config()
            # now there are self._config and self._configdict

        ####################################
        ## Initialize optional parameters ##
        ####################################

        #########################
        ## Initialize commands ##
        #########################

        # print(args =", args)

        # it seems there is no easy way to set a default command
        # for a subparser
        if args.command_name == "remote" and args.command is None:
            self._func = self._remote_show
        else:
            self._func = args.func

        if args.command_name == "init":
            reponame = args.reponame
            if reponame is None:
                reponame = socket.gethostname()
            # todo: what if reponame is empty?
            # probably ask interactively or ask
            # for a command line option.
            self.reponame = reponame
        elif args.command_name == "pull":
            args._remote = args.source
        elif args.command_name == "push":
            args._remote = args.destination

        self._args = args

        # self._parser = parser

    def _add_remote(self, remote, path):
        """Add a remote and its path to the config file."""
        # from https://docs.python.org/2.7/library/configparser.html#examples
        config = configparser.RawConfigParser()
        config.add_section(remote)
        config.set(remote, "path", path)
        with open(self.CONFIGFILE, "a") as configfile:
            config.write(configfile)

    def _commit(self):
        """Commit the working directory and log that."""
        # commit directory name is based on UNIX time
        # date = datetime.date.today()
        # date_str = "{}{:#02}{:#02}".format(date.year, date.month, date.day)
        # print(date_str)

        short_commit_mess = self._args.message

        # platform.node() just calls socket.gethostname() with a check for errors.
        localhost = socket.gethostname()
        username = getpass.getuser()
        time_str = time.strftime(self.DATEFMT, time.localtime())

        log_str = "When: {date}\nWhere: {user}@{host}".format(
            date=time_str, user=username, host=localhost
        )
        if short_commit_mess:
            short_commit_mess += "\n\n"
        short_commit_mess += log_str

        if not os.path.exists(self.COMMITDIR):
            os.mkdir(self.COMMITDIR, self.DIRMODE)

        commit_name = str(int(time.time()))
        commit_dir = os.path.join(self.COMMITDIR, commit_name)
        commit_dir_tmp = commit_dir + "_tmp"

        # Raise if this commit exists
        # We don't want rsync to write twice to one commit
        # even if it's hard to imagine how this could be possible
        # (probably broken clock?)
        if os.path.exists(commit_dir):
            raise RuntimeError("commit {} exists".format(commit_dir))
        elif os.path.exists(commit_dir_tmp):
            raise RuntimeError(
                "temporary commit {} exists".format(commit_dir_tmp)
            )

        # exclude .ys, otherwise an empty .ys/ will appear in the commit
        command = ["rsync", "-a", "--link-dest=../../..", "--exclude=/.ys"]
        full_command_str = " ".join(command)

        filter_list, filter_str = self._get_filter(include_commits=False)
        command.extend(filter_list)
        if filter_str:
            full_command_str += " " + filter_str
        # the trailing slash is very important for rsync
        # on Windows the separator is the same for rsync.
        # https://stackoverflow.com/a/59987187/952234
        # However, this may or may not work in cygwin
        # https://stackoverflow.com/a/18797771/952234
        root_dir = self.root_dir + '/'
        command.append(root_dir)
        command.append(commit_dir_tmp)
        full_command_str += " " + root_dir + " " + commit_dir_tmp

        self._print(full_command_str)
        # self._print("command =", command, debug=True)
        completed_process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
        # The data read is buffered in memory,
        # so do not use this method if the data size is large or unlimited.
        # https://docs.python.org/2/library/subprocess.html?highlight=subprocess#subprocess.Popen.communicate
        stdoutdata, stderrdata = completed_process.communicate()
        returncode = completed_process.returncode
        if returncode:
            self._print_error("an error occurred during hard linking, "
                              "rsync returned {}".format(returncode))
            return returncode

        # commit is done
        self._print("mv {} {}".format(commit_dir_tmp, commit_dir))
        os.rename(commit_dir_tmp, commit_dir)

        ## log ##
        if not os.path.exists(self.LOGDIR):
            self._print("mkdir {}".format(self.LOGDIR))
            os.mkdir(self.LOGDIR, self.DIRMODE)

        commit_log_name = os.path.join(self.LOGDIR, commit_name + ".txt")

        # write log file
        with open(commit_log_name, "w") as commit_file:
            print(short_commit_mess, file=commit_file)

        # print to stdout
        self._print(
            "commit {} created\n\n".format(commit_name),
            short_commit_mess,
            sep=""
        )

        return 0

    def _get_last_commit(self, commits=None):
        if commits is None:
            commits = self._get_local_commits()
        if not commits:
            return None
        return max(commits)

    def _get_last_sync(self):
        with open(self.SYNCFILENAME) as fil:
            data = fil.readlines()[0].strip()  # remove trailing newline
            commit, repo = data.split(sep=",", maxsplit=1)
        return (int(commit), repo)

    def _get_local_commits(self):
        # return integer representations of local commits
        # Take care to materialize the list if using the results twice!
        try:
            # listdir always returns a list (Python 2 and 3)
            commit_candidates = os.listdir(self.COMMITDIR)
        except EnvironmentError:
            # no commits exist
            commit_candidates = []
        return map(int, filter(_is_commit, commit_candidates))

    def _get_filter(self, include_commits=True):
        # todo: .ys, commits and logs should not be fixed here.
        if os.path.exists(self.RSYNCFILTER):
            filter_ = ["--filter=merge {}".format(self.RSYNCFILTER)]
            filter_str = "--filter='merge {}'".format(self.RSYNCFILTER)
        else:
            filter_ = []
            filter_str = ""
        if include_commits:
            includes = ["/.ys/commits", "/.ys/logs"]
            include_commands = ["--include={}".format(inc) for inc in includes]
            # since we don't have spaces in the command,
            # single ticks are not necessary
            include_command_str = " ".join(["--include={}".format(inc) for inc in includes])
            filter_ += include_commands
            if filter_str:
                # otherwise an empty string will be joined by a space
                filter_str = " ".join([filter_str, include_command_str])
            else:
                filter_str = include_command_str
        # exclude can go before or after include,
        # because the first matching rule is applied.
        # It's important to place /* after .ys,
        # because it means files exactly one level below .ys
        filter_ += ["--exclude=/.ys/*"]
        if filter_str:
            filter_str += " "
        filter_str += "--exclude=/.ys/*"
        # print("filter_str: '{}'".format(filter_str))
        return (filter_, filter_str)

    def _get_dest_path(self, dest=None):
        """Return a pair *(host, destpath)*, where
        *host* is a real host (its ip/name/etc.) at the destination
        and *destpath* is a path on that host.

        If *host* is not present in the configuration,
        it is set to localhost.
        If destination path is missing, `KeyError` is raised.
        """
        config = self._configdict
        if not dest:
            try:
                dest = config["default"]["name"]
            except KeyError:
                raise KeyError(
                    'no default destination found in config. '
                    'Add subsection "default" with name=<default subsection>'
                )
            # This doesn't give the first section, helas.
            # defaultsect = list(configdict.items())[0]
            # dest = defaultsect[0]

        try:
            destpath = config[dest]["path"]
        except KeyError:
            raise KeyError("destination path must be present "
                           "in the configuration") from None
        try:
            host = config[dest]["host"]
        except KeyError:
            # localhost
            host = ""

        # destpath = "{}:{}".format(dest, configdict[remote]["path"])
        if not destpath.endswith('/'):
            destpath += '/'
        return (host, destpath)

    def _get_root_directory(self, sync_dir):
        cur_path = os.getcwd()
        # path without symlinks
        root_path = os.path.realpath(cur_path)
        while True:
            test_path = os.path.join(root_path, sync_dir)
            if os.path.exists(test_path):
                # without trailing slash
                return root_path
            if os.path.dirname(root_path) == root_path:
                # won't work on Windows shares with '\\server\share',
                # but ignore.
                # https://stackoverflow.com/a/10803459/952234
                break
            root_path = os.path.dirname(root_path)
        raise OSError(
            "configuration directory {} not found".format(sync_dir)
        )

    def _init(self):
        """Initialize default configuration.

        Create configuration folder, configuration and repository files.

        If a configuration file already exists, it is not changed.
        This operation is safe and idempotent (can be repeated safely).
        """
        # todo: create example configuration file.
        ysdir = self.config_dir
        repofile = self.REPOFILE
        reponame = self.reponame

        self._print("# init configuration for {}".format(reponame))

        # create config_dir
        if not os.path.exists(ysdir):
            self._print("mkdir {}".format(ysdir))
            os.mkdir(ysdir, self.DIRMODE)
        else:
            self._print("{} already exists, skip".format(ysdir))

        # create self.CONFIGFILE
        if not os.path.exists(self.CONFIGFILE):
            self._print("create configuration file {}".format(self.CONFIGFILE))
            with open(self.CONFIGFILE, "w") as fil:
                print("", end="", file=fil)
        else:
            self._print("{} already exists, skip".format(self.CONFIGFILE))

        # create self.REPOFILE
        if not os.path.exists(repofile):
            self._print("create configuration file {}".format(repofile))
            with open(repofile, "w") as fil:
                print(reponame, end="", file=fil)
        else:
            self._print("{} already exists, skip".format(repofile))

        return 0

    def _make_commit_list(self, commits=None, logs=None):
        """Make a list of (commit, commit_log).

        Commits and logs are sorted lists of integers.

        If a commit_log is missing for a given commit,
        ``None`` is used."""
        # commits and logs in the interface are
        # just for testing purposes

        def get_sorted_logs_int(files):
            stripped_files = (fil[:-4] for fil in files)
            return sorted(map(int, filter(_is_commit, stripped_files)))

        if commits is None:
            commits = sorted(self._get_local_commits())

        if logs is None:
            try:
                log_files = os.listdir(self.LOGDIR)
            except OSError:
                # no log directory exists
                log_files = []
            logs = get_sorted_logs_int(log_files)

        if not commits and not logs:
            return []

        results = []
        commit_ind = 0
        commits_len = len(commits)
        log_ind = 0
        logs_len = len(logs)
        commit = None
        log = None

        while True:
            logs_finished = (log_ind > logs_len - 1)
            commits_finished = (commit_ind > commits_len - 1)
            if not commits_finished:
                commit = commits[commit_ind]
            if not logs_finished:
                log = logs[log_ind]

            if commits_finished and logs_finished:
                break
            elif logs_finished:
                results.append((commit, None))
                commit_ind += 1
                continue
            elif commits_finished:
                results.append((None, log))
                log_ind += 1
                continue
            # print(commit_ind, log_ind)

            # both commits and logs are present
            if commit == log:
                results.append((commit, log))
                commit_ind += 1
                log_ind += 1
            elif commit < log:
                results.append((commit, None))
                commit_ind += 1
            else:
                results.append((None, log))
                log_ind += 1
        return results

    def _log(self):
        """Print commits and log information.

        If only a commit or only a log are present, they are listed as well.

        By default most recent commits are printed first.
        Set *reverse* to ``False`` to print last commits last.
        """
        reverse = self._args.reverse
        max_count = self._args.max_count

        cl_list = self._make_commit_list()

        if not reverse:
            commit_log_list = list(reversed(cl_list))
        else:
            commit_log_list = cl_list
        if max_count != -1:
            # otherwise the last element is excluded
            commit_log_list = commit_log_list[:max_count]

        try:
            synced_commit, remote = self._get_last_sync()
        except EnvironmentError:
            self._print("# no syncronization information found")
            synced_commit, remote = (None, None)

        def print_logs(commit_log_list):
            for ind, (commit, log) in enumerate(commit_log_list):
                if ind:
                    self._print()
                if commit is None:
                    commit_str = "commit {} is missing".format(log)
                    commit = log
                else:
                    if commit == synced_commit:
                        sync_str = "<-> {}".format(remote)
                        commit_str = "commit {} {}".format(commit, sync_str)
                    else:
                        commit_str = "commit " + str(commit)
                if log is None:
                    log_str = "Log is missing"
                    # time.time is timezone independent.
                    # Therefore localtime is the local time
                    # corresponding to that universal time.
                    # Commit could be made in any time zone.
                    commit_time_str = time.strftime(self.DATEFMT, time.localtime(commit))
                    log_str += "\nWhen: {}".format(commit_time_str) + '\n'
                else:
                    log_file = open(os.path.join(self.LOGDIR, str(log) + ".txt"))
                    # read returns a redundant newline
                    log_str = log_file.read()
                    # print("log_str: '{}'".format(log_str))
                self._print(commit_str, log_str, sep='\n', end='')

        print_logs(commit_log_list)

        if not commit_log_list:
            self._print("No commits found")

        # in fact, sys.exit(None) still returns 0 to the shell
        return 0

    def _print(self, *args, **kwargs):
        debug = kwargs.pop("debug", False)
        if debug and not self.DEBUG:
            return
        # print(str(stdoutdata, 'utf-8'), end="")
        print(*args, **kwargs)

    def _print_error(self, msg):
        print("!", msg)

    def _pull_push(self):
        # actually, this was how it is below. No link-dest during push.
        # In fact, it is not needed.
        # If a file is new, it won't be in remote commits.
        # -H preserves hard links in one set of files (but see the note in todo.txt)

        returncode, changed = self._status(check_changed=True, verbose=False)
        if changed:
            raise OSError("local repository has uncommited changes")
        if returncode:
            raise OSError("could not check for uncommited changes, "
                          "rsync returned {}".format(returncode))

        remote = self._args._remote

        try:
            host, destpath = self._get_dest_path(remote)
        except KeyError as err:
            raise err from None
            # # a local path. Though we can't be sure the host is called localhost.
            # remote, destpath = None, remote
            # host = ""
            # if destpath and destpath[-1] != os.sep:
            #     destpath += os.sep  # '/' for Linux
        full_destpath = _mkhostpath(host, destpath)

        # print("host, destpath, remote =", host, destpath, remote)
        new = self._args.new
        # if there exists .ys/rsync-filter, command will need quotes,
        # but they are present there
        filter_, filter_str = self._get_filter()

        command = ["rsync", "-avHP"]
        if self._args.dry_run:
            command += ["-n"]
        command_str = " ".join(command)
        if not new:
            command.append("--delete-after")
            command_str += " --delete-after"
        command.extend(filter_)
        command_str += " " + filter_str
        root_path = self.root_dir + "/"
        if self._args.command_name == "push":
            command.append(root_path)
            command.append(full_destpath)
            command_str += " {} {}".format(root_path, full_destpath)
        else:  # pull
            command.append(full_destpath)
            command.append(root_path)
            command_str += " {} {}".format(full_destpath, root_path)

        # self._print("#", command, debug=True)
        self._print("#", command_str)

        # todo: do we need all missing commits?
        # We should look at only the most recent commits.
        remote_commits = os.path.join(full_destpath, ".ys", "commits" + '/')
        if self._args.command_name  == "push":
            missing_commits = self._test_missing_commits(remote_commits,
                                                         self.COMMITDIR + '/')
        else:
            missing_commits = self._test_missing_commits(self.COMMITDIR + '/',
                                                         remote_commits)
        if missing_commits:
            raise OSError("destination has commits missing on source: {}"\
                          .format(", ".join(missing_commits)) +
                          ", synchronize these commits first"
                         )

        completed_process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
        stdoutdata, stderrdata = completed_process.communicate()
        returncode = completed_process.returncode
        if returncode:
            self._print_error("an error occurred, rsync returned {}".format(returncode))
            return returncode

        last_commit = self._get_last_commit()
        if last_commit is not None:
            sync_str = "{},{}".format(last_commit, remote)
            try:
                with open(self.SYNCFILENAME, "w") as fil:
                    print(sync_str, end="", file=fil)
            except OSError:
                self._print_error("data transferred, but could not log to {}"
                                  .format(self.SYNCFILENAME))

        return 0

    def _read_config(self):

        # substitute environmental variables (if present and available)
        # todo: many problems. Env variables don't have to be available
        # for all sections. On the other hand, they must be present
        # for the options currently used.
        with open(self.CONFIGFILE, "r") as conf_file:
            subst_lines = _substitute_env(conf_file.read()).getvalue()

        config = configparser.ConfigParser()
        # ConfigParser.read_string
        # is undocumented in Python2, but present!
        config.read_string(subst_lines)

        # not sure whether I need this
        config["DEFAULT"]["srcpath"] = self.root_dir  # "./"
        config["DEFAULT"]["exclude"] = self.config_dir
        configdict = {}
        for section in config.sections():
            sectiond = dict(config[section])
            configdict[section] = sectiond
            if section == "default":
                continue

            try:
                # all local repositories must have an entry "host"
                # (can be empty or localhost, or equivalent)
                host = sectiond["host"]
            except KeyError:
                # sections for remotes can be named after their hosts
                host = section
            # host = self._get_remote_host(section)
            destpath = sectiond["path"]
            sectiond["destpath"] = _mkhostpath(host, destpath)

        # print all values:
        # configdict = self._configdict
        # formatter = lambda s: json.dumps(s, sort_keys=True, indent=4)
        # print(formatter(configdict))

        # config.items() includes the DEFAULT section, which can't be removed.
        self._config = config
        self._configdict = configdict
        # return would be more flexible if the path to config was passed
        # as an argument to this function
        # return configdict

    def _remote(self):
        if sys.argv[2] == "add":
            self._add_remote(sys.argv[3], sys.argv[4])
        elif sys.argv[2] == "-v":
            _print_remotes()
            return 0

    def _remote_show(self):
        """Print names of remotes. If verbose, print paths as well."""
        config = self._configdict
        if self._args.verbose:
            for section, options in config.items():
                print(section, options["destpath"], sep="\t")
        else:
            for section in self._config.sections():
                print(section)

    def _status(self, check_changed=False, verbose=True):
        """Print files and directories that were updated more recently
        than last commit.

        If no yarsync configuration is found, an error is printed
        and 7 returned.

        Return exit code of `rsync`.
        If *check_changed* is `True`,
        return a tuple *(returncode, changed)*,
        where *changed* is `True`
        if the working directory has changes since last commit.

        If *verbose* is `False`,
        output is printed only in case of changes.
        """
        # We don't return an error if the directory has changed,
        # because it is a normal situation (not an error).
        # This is the same as in git.

        if os.path.exists(self.COMMITDIR):
            commit_subdirs = [fil for fil in os.listdir(self.COMMITDIR)
                              if _is_commit(fil)]
        else:
            commit_subdirs = []

        ## no commits is fine for an initial commit
        if not commit_subdirs:
            # if verbose:
            print("No commits found")
            if check_changed:
                return (0, True)
            return 0

        newest_commit = max(map(int, commit_subdirs))
        newest_commit_dir = os.path.join(self.COMMITDIR, str(newest_commit))
        filter_command, filter_str = self._get_filter(include_commits=False)

        command_begin = [
            "rsync", "-aun", "--delete", "-i", "--exclude=/.ys"
        ]
        command_str = " ".join(command_begin)

        command = command_begin + filter_command
        if filter_str:
            command_str += " " + filter_str

        # outbuf option added in Rsync 3.1.0 (28 Sep 2013)
        # https://download.samba.org/pub/rsync/NEWS#ENHANCEMENTS-3.1.0
        # from https://stackoverflow.com/a/35775429
        command.append('--outbuf=L')
        command_str += " --outbuf=L"

        root_path = self.root_dir + "/"
        command_end = [root_path, newest_commit_dir]
        command += command_end
        command_str += " " + " ".join(command_end)

        if verbose:
            print(command_str)

        sp = subprocess.Popen(command, stdout=subprocess.PIPE)
        # changed means there were actual changes in the working dir
        changed = False
        # note that directories may appear to be changed
        # just because of timestamps (add and remove a file), e.g.
        # b'.d..t...... ./\n'
        # b'' means EOF in the iteration.
        lines = iter(sp.stdout.readline, b'')
        for line in lines:
            if line:
                print("Changed since last commit:")
                # skip permissions
                if not line.startswith(b'.'):
                # if not line.startswith(b'.d..t......'):
                    changed = True

                # print the line and all following lines.
                # todo: use terminal encoding
                print(line.decode("utf-8"), end='')
                # in fact, readline could be used twice.
                for line in lines:
                    if not line.startswith(b'.d..t......'):
                        changed = True
                    print(line.decode("utf-8"), end='')

        returncode = sp.returncode

        if not changed:
            self._print("Nothing to commit, working directory clean.")

        try:
            synced_commit, repo = self._get_last_sync()
        except EnvironmentError:
            self._print("No syncronization information found.")
        else:
            commits = list(self._get_local_commits())
            last_commit = self._get_last_commit(commits)
            if synced_commit == last_commit:
                self._print("\nCommits are up to date with {}."\
                            .format(repo))
            else:
                n_newer_commits = sum([1 for comm in commits
                                       if comm > synced_commit])
                self._print("# current repository is {} commits ahead of {}"\
                            .format(n_newer_commits, repo))

        # called from an internal method
        if check_changed:
            return (returncode, changed)
        # called as the main command
        return returncode

    def _test_missing_commits(self, from_path, to_path):
        """Return a list of commits (directories) present on *from_path*
        and missing on *to_path*."""
        # self._print("test missing:", debug=True)
        command = "rsync -nr --info=NAME --include=/ --exclude=/*/*".split() \
                  + [from_path, to_path]
        # self._print(" ".join(command), debug=True)
        completed_process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
        stdoutdata, stderrdata = completed_process.communicate()
        returncode = completed_process.returncode
        raw_names = stdoutdata.split()
        # commit folder can have files from the user
        # (maybe it should not be allowed: are they transferred at all?..)
        dirs = (os.path.dirname(str(dir_, 'utf-8')) for dir_ in raw_names)
        missing_commits = [os.path.basename(dir_) for dir_ in dirs if dir_]
        return missing_commits

    def __call__(self):
        """Call the command set during the initialization.

        Return values:
        8 - not a yarsync directory or an OS error
        """
        # 7 is returned in case of bad argparse
        try:
            # all errors are usually transferred as returncode
            # and functions throw no exceptions
            returncode = self._func()
        # in Python 3 EnvironmentError is an alias to OSError
        except EnvironmentError as err:
            # In Python 3 there are more errors, e.g. PermissionError, etc.
            # PermissionError belongs to OSError in Python 3,
            # but to IOError in Python 2.
            self._print_error(err)
            returncode = 8
        return returncode
