# Koji callback for sending notifications about events to a messagebus (amqp broker)
# Copyright (c) 2009 Red Hat, Inc.
#
# Authors:
#     Mike Bonnet <mikeb@redhat.com>

from koji.plugin import callbacks, callback, ignore_error
import ConfigParser
import logging
import qpid
import qpid.util
import qpid.connection
import qpid.datatypes
try:
    import json
except ImportError:
    import simplejson as json

MAX_KEY_LENGTH = 255
CONFIG_FILE = '/etc/koji-hub/plugins/messagebus.conf'

config = None
connection = None

def get_session():
    global connection, config
    if connection:
        try:
            return connection.session('koji-' + str(qpid.datatypes.uuid4()))
        except:
            logging.getLogger('koji.plugin.messagebus').warning('Error getting session, will retry', exc_info=True)
            connection = None

    config = ConfigParser.SafeConfigParser()
    config.read(CONFIG_FILE)

    sock = qpid.util.connect(config.get('broker', 'host'),
                             int(config.get('broker', 'port')))
    if config.getboolean('broker', 'ssl'):
        sock = qpid.util.ssl(sock)
    conn_opts = {'sock': sock, 'mechanism': config.get('broker', 'auth')}
    if conn_opts['mechanism'] == 'PLAIN':
        conn_opts['username'] = config.get('broker', 'username')
        conn_opts['password'] = config.get('broker', 'password')
    conn = qpid.connection.Connection(**conn_opts)
    conn.start()
    session = conn.session('koji-' + str(qpid.datatypes.uuid4()))

    session.exchange_declare(exchange=config.get('exchange', 'name'),
                             type=config.get('exchange', 'type'),
                             durable=config.getboolean('exchange', 'durable'))

    connection = conn

    return session

def get_routing_key(cbtype, *args, **kws):
    global config
    key = [config.get('queues', 'prefix'), cbtype]
    if cbtype in ('prePackageListChange', 'postPackageListChange'):
        key.append(kws['tag']['name'])
        key.append(kws['package']['name'])
    elif cbtype in ('preTaskStateChange', 'postTaskStateChange'):
        key.append(kws['attribute'])
        key.append(str(kws['old']))
        key.append(str(kws['new']))
    elif cbtype in ('preBuildStateChange', 'postBuildStateChange'):
        info = kws['info']
        key.append(info['name'])
        key.append(info['version'])
        key.append(info['release'])
        key.append(kws['attribute'])
        key.append(str(kws['old']))
        key.append(str(kws['new']))
    elif cbtype in ('preImport', 'postImport'):
        key.append(kws['type'])
    elif cbtype in ('preTag', 'postTag', 'preUntag', 'postUntag'):
        key.append(kws['tag']['name'])
        build = kws['build']
        key.append(build['name'])
        key.append(build['version'])
        key.append(build['release'])
        key.append(kws['user']['name'])
    elif cbtype in ('preRepoInit', 'postRepoInit'):
        key.append(kws['tag']['name'])
    elif cbtype in ('preRepoDone', 'postRepoDone'):
        key.append(kws['repo']['tag_name'])

    # ensure the routing key is an ascii string with a maximum
    # length of 255 characters
    key = '.'.join(key)
    key = key[:MAX_KEY_LENGTH]
    key = key.encode('ascii', 'xmlcharrefreplace')
    return key

def encode_data(data):
    global config
    format = config.get('format', 'encoding')
    if format == 'json':
        return json.dumps(data)
    else:
        raise koji.PluginError, 'unsupported encoding: %s' % format

@callback(*callbacks.keys())
@ignore_error
def send_message(cbtype, *args, **kws):
    global config
    session = get_session()
    routing_key = get_routing_key(cbtype, *args, **kws)
    props = session.delivery_properties(routing_key=routing_key)
    data = kws.copy()
    if args:
        data['args'] = list(args)
    payload = encode_data(data)
    message = qpid.datatypes.Message(props, payload)
    session.message_transfer(destination=config.get('exchange', 'name'), message=message)
    session.close()
