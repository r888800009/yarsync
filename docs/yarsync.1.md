% YARSYNC(1) yarsync 0.1
% Written by Yaroslav Nikitenko
% June 2022

# NAME
Yet Another Rsync is a file synchronization tool

# SYNOPSIS
**yarsync** [-h] [-n] [-q] command [args]

# DESCRIPTION
**yarsync** is a wrapper around rsync to store configuration
and synchronize repositories with the interface similar to git.
It is efficient (files in the repository can be removed and renamed freely without additional transfers)
and distributed (several replicas of the repository can diverge, and in that case a manual merge is supported).

[comment]: # (**yarsync** stores snapshot versions in commits in .ys/commits subdirectory. It is non-intrusive)

# OPTIONS
**-h**, **--help**
: Prints help message and exits.

# EXIT STATUS
**0**
: Success

**1**
: Invalid option

In case of rsync errors, yarsync returns the corresponding rsync error code.

# SEE ALSO
**rsync**(1)

The yarsync page is <https://github.com/ynikitenko/yarsync>.

# BUGS
Please report bugs to <https://github.com/ynikitenko/yarsync/issues>.

# COPYRIGHT
Copyright © 2021-2022 Yaroslav Nikitenko.
License GPLv3+: GNU GPL version 3 or later <https://gnu.org/licenses/gpl.html>.\
This  is free software: you are free to change and redistribute it.  There is NO
WARRANTY, to the extent permitted by law.
