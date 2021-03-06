#!/usr/bin/python
#
# Create relations between a shared database to many peers.
# Join does nothing.   Peer requests access to $DATABASE from $REMOTE_HOST.
# It's up to the hooks to ensure database exists, peer has access and
# clean up grants after a broken/departed peer (TODO)
#
# Author: Adam Gandelman <adam.gandelman@canonical.com>


from common import (
    database_exists,
    create_database,
    grant_exists,
    create_grant)
import subprocess
import json
import socket
import os
import lib.utils as utils
import lib.cluster_utils as cluster
from charmhelpers.core import hookenv

LEADER_RES = 'res_mysql_vip'


def pwgen():
    return str(subprocess.check_output(['pwgen', '-s', '16'])).strip()


def relation_get():
    return json.loads(subprocess.check_output(['relation-get',
                                               '--format',
                                               'json']))


def unit_sorted(units):
    """Return a sorted list of unit names."""
    return sorted(
        units, lambda a, b: cmp(int(a.split('/')[-1]), int(b.split('/')[-1])))


def get_unit_addr(relid, unitid):
    return hookenv.relation_get(attribute='private-address',
                                unit=unitid,
                                rid=relid)


def shared_db_changed():

    def get_allowed_units(database, username):
        allowed_units = set()
        for relid in hookenv.relation_ids('shared-db'):
            for unit in hookenv.related_units(relid):
                if grant_exists(database,
                                username,
                                get_unit_addr(relid, unit)):
                    allowed_units.add(unit)
        return allowed_units

    def configure_db(hostname,
                     database,
                     username):
        passwd_file = "/var/lib/mysql/mysql-{}.passwd".format(username)
        if hostname != local_hostname:
            remote_ip = socket.gethostbyname(hostname)
        else:
            remote_ip = '127.0.0.1'

        if not os.path.exists(passwd_file):
            password = pwgen()
            with open(passwd_file, 'w') as pfile:
                pfile.write(password)
                os.chmod(pfile.name, 0600)
        else:
            with open(passwd_file) as pfile:
                password = pfile.read().strip()

        if not database_exists(database):
            create_database(database)
        if not grant_exists(database,
                            username,
                            remote_ip):
            create_grant(database,
                         username,
                         remote_ip, password)
        return password

    if not cluster.eligible_leader(LEADER_RES):
        utils.juju_log('INFO',
                       'MySQL service is peered, bailing shared-db relation'
                       ' as this service unit is not the leader')
        return

    settings = relation_get()
    local_hostname = utils.unit_get('private-address')
    singleset = set([
        'database',
        'username',
        'hostname'])

    if singleset.issubset(settings):
        # Process a single database configuration
        password = configure_db(settings['hostname'],
                                settings['database'],
                                settings['username'])
        allowed_units = " ".join(unit_sorted(get_allowed_units(
            settings['database'],
            settings['username'])))
        if not cluster.is_clustered():
            utils.relation_set(db_host=local_hostname,
                               password=password,
                               allowed_units=allowed_units)
        else:
            utils.relation_set(db_host=utils.config_get("vip"),
                               password=password,
                               allowed_units=allowed_units)

    else:
        # Process multiple database setup requests.
        # from incoming relation data:
        #  nova_database=xxx nova_username=xxx nova_hostname=xxx
        #  quantum_database=xxx quantum_username=xxx quantum_hostname=xxx
        # create
        # {
        #   "nova": {
        #        "username": xxx,
        #        "database": xxx,
        #        "hostname": xxx
        #    },
        #    "quantum": {
        #        "username": xxx,
        #        "database": xxx,
        #        "hostname": xxx
        #    }
        # }
        #
        databases = {}
        for k, v in settings.iteritems():
            db = k.split('_')[0]
            x = '_'.join(k.split('_')[1:])
            if db not in databases:
                databases[db] = {}
            databases[db][x] = v
        return_data = {}
        allowed_units = []
        for db in databases:
            if singleset.issubset(databases[db]):
                return_data['_'.join([db, 'password'])] = \
                    configure_db(databases[db]['hostname'],
                                 databases[db]['database'],
                                 databases[db]['username'])
                return_data['_'.join([db, 'allowed_units'])] = \
                    " ".join(unit_sorted(get_allowed_units(
                        databases[db]['database'],
                        databases[db]['username'])))
        if len(return_data) > 0:
            utils.relation_set(**return_data)
        if not cluster.is_clustered():
            utils.relation_set(db_host=local_hostname)
        else:
            utils.relation_set(db_host=utils.config_get("vip"))

hooks = {"shared-db-relation-changed": shared_db_changed}

utils.do_hooks(hooks)
