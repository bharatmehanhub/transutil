import os
import stat
import errno


class Error(OSError):
    pass


class SameFileError(Error):
    """In case source and destination are same files."""


class SpecialFileError(OSError):
    """In case we are trying to copy a special file (e.g. a named pipe)"""


class File:
    def __init__(self, source):
        self.source_path = source
        self.target_path = None

    def _samefile(self, dst):
        # Macintosh, Unix.
        if isinstance(self.source_path, os.DirEntry) and hasattr(os.path, 'samestat'):
            try:
                return os.path.samestat(self.source_path.stat(), os.stat(dst))
            except OSError:
                return False

        if hasattr(os.path, 'samefile'):
            try:
                return os.path.samefile(self.source_path, dst)
            except OSError:
                return False

        # All other platforms: check for same pathname.
        return (os.path.normcase(os.path.abspath(self.source_path)) ==
                os.path.normcase(os.path.abspath(dst)))

    @staticmethod
    def copyfileobj(fsrc, fdst, length=16*1024):
        """Copy data from file-like object fsrc to file-like object fdst"""
        while 1:
            buf = fsrc.read(length)
            if not buf:
                break
            fdst.write(buf)

    if hasattr(os, 'listxattr'):

        def _copyxattr(self, dst, *, follow_symlinks=True):
            """Copy extended filesystem attributes from `self.source_path` to `dst`.
            Overwrite existing attributes.
            If `follow_symlinks` is false, symlinks won't be followed.
            """
            try:
                names = os.listxattr(self.source_path, follow_symlinks=follow_symlinks)
            except OSError as e:
                if e.errno not in (errno.ENOTSUP, errno.ENODATA, errno.EINVAL):
                    raise
                return
            for name in names:
                try:
                    value = os.getxattr(self.source_path, name, follow_symlinks=follow_symlinks)
                    os.setxattr(dst, name, value, follow_symlinks=follow_symlinks)
                except OSError as e:
                    if e.errno not in (errno.EPERM, errno.ENOTSUP, errno.ENODATA,
                                       errno.EINVAL):
                        raise
    else:
        def _copyxattr(self, *args, **kwargs):
            pass

    def copyfile(self, dst, *, copy_meta=False, follow_symlinks=True):
        self.target_path = dst
        if os.path.isdir(dst):
            dst = os.path.join(dst, os.path.basename(self.source_path))

        if self._samefile(dst):
            raise SameFileError("{!r} and {!r} are the same file".format(self.source_path, dst))
        for fn in [self.source_path, self.target_path]:
            try:
                st = os.stat(fn)
            except OSError:
                # File most likely does not exist
                pass
            else:
                # XXX What about other special files? (sockets, devices...)
                if stat.S_ISFIFO(st.st_mode):
                    raise SpecialFileError("`%s` is a named pipe" % fn)
        if not follow_symlinks and os.path.islink(self.source_path):
            os.symlink(os.readlink(self.source_path), self.target_path)
        else:
            with open(self.source_path, 'rb') as fsrc:
                with open(self.target_path, 'wb') as fdst:
                    self.copyfileobj(fsrc, fdst)

        if copy_meta:

            def _nop(*args, ns=None, follow_symlinks=None):
                pass

            # follow symlinks (aka don't not follow symlinks)
            follow = follow_symlinks or not (os.path.islink(self.source_path) and os.path.islink(dst))
            if follow:
                # use the real function if it exists
                def lookup(name):
                    return getattr(os, name, _nop)
            else:

                def lookup(name):
                    fn = getattr(os, name, _nop)
                    if fn in os.supports_follow_symlinks:
                        return fn
                    return _nop
            st = lookup("stat")(self.source_path, follow_symlinks=follow)
            mode = stat.S_IMODE(st.st_mode)
            lookup("utime")(dst, ns=(st.st_atime_ns, st.st_mtime_ns),
                            follow_symlinks=follow)

            self._copyxattr(dst, follow_symlinks=follow)

            try:
                lookup("chmod")(dst, mode, follow_symlinks=follow)
            except NotImplementedError:
                pass
            if hasattr(st, 'st_flags'):
                try:
                    lookup("chflags")(dst, st.st_flags, follow_symlinks=follow)
                except OSError as why:
                    for err in 'EOPNOTSUPP', 'ENOTSUP':
                        if hasattr(errno, err) and why.errno == getattr(errno, err):
                            break
                    else:
                        raise

        return dst