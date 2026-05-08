try:
    import pymysql

    pymysql.install_as_MySQLdb()
    pymysql.__version__ = '2.2.4'
    if hasattr(pymysql, 'VERSION'):
        pymysql.VERSION = (2, 2, 4, 'final', 0)
    if hasattr(pymysql, 'version_info'):
        pymysql.version_info = (2, 2, 4, 'final', 0)
except ModuleNotFoundError:
    pass

try:
    from .celery import app as celery_app
except ModuleNotFoundError:
    celery_app = None

__all__ = ('celery_app',) if celery_app is not None else ()
