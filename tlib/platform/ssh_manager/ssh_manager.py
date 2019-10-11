# !/usr/bin/env python
# -*- coding: utf-8 -*-
# @Time    : 2019/10/10 15:40
# @Author  : Tao.Xu
# @Email   : tao.xu2008@outlook.com

""" paramiko ssh """

import re
import paramiko
import scp
import inspect
import unittest

from tlib import log
from tlib.retry import retry, retry_call
from tlib.utils import util

# =============================
# --- Global
# =============================
logger = log.get_logger()


class SSHObj(object):
    """
    SSH run cmd and scp
    """
    _ssh = None

    def __init__(self, ip, username, password=None, key_file=None, port=22,
                 conn_timeout=1200):
        self.ip = util.get_reachable_ip(ip, ping_retry=3) \
            if isinstance(ip, list) else ip
        self.username = username
        self.password = password
        self.key_file = key_file
        self.port = port
        self.conn_timeout = conn_timeout

    def __del__(self):
        # logger.debug('Enter SSHObj.__del__()')
        # self.ssh.close()
        del self._ssh

    @property
    def ssh(self):
        if self._ssh is None or self._ssh.get_transport() is None or \
                not self._ssh.get_transport().is_active():
            self._ssh = self.connect()
        return self._ssh

    @retry(tries=10, delay=3, jitter=1)
    def connect(self):
        logger.info('SSH Connect to {0}@{1}(pwd:{2}, key_file:{3})'.format(
            self.username, self.ip, self.password,
            self.key_file))
        compile_ip = re.compile(r'^((25[0-5]|2[0-4]\d|[01]?\d\d?)\.){3}(25[0-5]|2[0-4]\d|[01]?\d\d?)$')
        if not compile_ip.match(self.ip):
            logger.error('Error IP address!')
            return None

        _ssh = paramiko.SSHClient()
        # _ssh.load_system_host_keys()
        _ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())

        try:
            if self.key_file is not None:
                pkey = paramiko.RSAKey.from_private_key_file(self.key_file)
                _ssh.connect(self.ip, self.port, self.username, self.password,
                             timeout=self.conn_timeout, pkey=pkey)
            else:
                _ssh.connect(self.ip, self.port, self.username, self.password,
                             timeout=self.conn_timeout)
        except Exception as e:
            logger.warning('SSH Connect {0} fail!'.format(self.ip))
            self._ssh = None
            raise e
        return _ssh

    @property
    def is_active(self):
        return self.ssh.get_transport().is_active()

    def paramiko_ssh_cmd(self, cmd_spec, timeout=7200, get_pty=False,
                         docker_image=None):
        """
        ssh to <ip> and then run commands --paramiko
        :param cmd_spec:
        :param timeout:
        :param get_pty:
        :param docker_image:
        :return:
        """

        sudo = False if self.username == 'root' else True
        # sudo = False if 'kubectl' in cmd_spec else sudo

        if docker_image:
            cmd_spec = "docker run -i --rm --network host " \
                       "-v /dev:/dev -v /etc:/etc " \
                       "--privileged {image} bash -c '{cmd}'".format(image=docker_image, cmd=cmd_spec)
        elif sudo:
            cmd_spec = 'sudo {cmd}'.format(cmd=cmd_spec)

        logger.info('Execute: ssh {0}@{1}# {2}'.format(self.username, self.ip,
                                                       cmd_spec))

        try:
            if sudo and (self.password or self.key_file):
                w_pwd = '' if self.key_file else self.password
                stdin, stdout, stderr = self.ssh.exec_command(
                    cmd_spec, get_pty=True, timeout=timeout)
                stdin.write(w_pwd + '\n')
                stdin.flush()
            else:
                stdin, stdout, stderr = self.ssh.exec_command(
                    cmd_spec, get_pty=get_pty, timeout=timeout)
                stdin.write('\n')
                stdin.flush()
            std_out = stdout.read().decode('UTF-8', 'ignore')
            std_err = stderr.read().decode('UTF-8', 'ignore')
            return std_out, std_err
        except Exception as e:
            raise Exception(
                'Failed to run command: {0}\n{1}'.format(cmd_spec, e))

    def ssh_cmd(self, cmd_spec, expected_rc=0, timeout=7200, get_pty=False,
                docker_image=None, tries=3, delay=3):
        """
        ssh and run cmd
        """
        method_name = inspect.stack()[1][
            3]  # Get name of the calling method, returns <methodName>'
        stdout, stderr = retry_call(self.paramiko_ssh_cmd,
                                    fkwargs={'cmd_spec': cmd_spec,
                                             'timeout': timeout,
                                             'get_pty': get_pty,
                                             'docker_image': docker_image},
                                    tries=tries, delay=delay, logger=logger)
        rc = -1 if stderr else 0
        output = stdout + stderr if stderr else stdout
        if isinstance(expected_rc, str) and expected_rc.upper() == 'IGNORE':
            return rc, output

        if rc != expected_rc:
            raise Exception('%s(): Failed command: %s\nMismatched '
                            'RC: Received [%d], Expected [%d]\nError: %s' % (
                    method_name, cmd_spec, rc, expected_rc, output))
        return rc, output

    @retry(tries=3, delay=1)
    def remote_scp_put(self, local_path, remote_path):
        """
        scp put --paramiko, scp
        :param local_path:
        :param remote_path:
        :return:
        """

        logger.info('scp %s %s@%s:%s' % (
        local_path, self.username, self.ip, remote_path))

        try:
            obj_scp = scp.SCPClient(self.ssh.get_transport())
            obj_scp.put(local_path, remote_path)

            # make sure the local and remote file md5sum match
            # local_md5 = util.md5sum(local_path)
            # rc, output = self.ssh_cmd('md5sum {0}'.format(remote_path), expected_rc=0)
            # remote_md5 = output.strip('\n').split(' ')[0]
            # logger.info('{0} {1}'.format(local_md5, local_path))
            # logger.info('{0} {1}'.format(remote_md5, remote_path))
            # assert remote_md5 == local_md5

            return True
        except Exception as e:
            raise e

    @retry(tries=3, delay=1)
    def remote_scp_get(self, local_path, remote_path):
        """
        scp get --paramiko, scp
        :param local_path:
        :param remote_path:
        :return:
        """

        logger.info('scp %s@%s:%s %s' % (
        self.username, self.ip, remote_path, local_path))

        try:
            obj_scp = scp.SCPClient(self.ssh.get_transport())
            obj_scp.get(remote_path, local_path)

            # make sure the local and remote file md5sum match
            # rc, output = self.ssh_cmd('md5sum {0}'.format(remote_path), expected_rc=0)
            # remote_md5 = output.strip('\n').split(' ')[0]
            # local_md5 = util.md5sum(local_path)
            # logger.info('{0} {1}'.format(remote_md5, remote_path))
            # logger.info('{0} {1}'.format(local_md5, local_path))
            # assert remote_md5 == local_md5

            return True
        except Exception as e:
            raise e


class SSHTestCase(unittest.TestCase):
    """docstring for SSHTestCase"""

    def setUp(self):
        pass

    def tearDown(self):
        pass

    def test_1(self):
        ssh_obj = SSHObj(ip='10.25.119.1', username='root',
                         password='password')
        rc, output = ssh_obj.ssh_cmd('pwd')
        logger.info(output)

        rc, output = ssh_obj.ssh_cmd('ls')
        logger.info(output)


if __name__ == '__main__':
    # test
    unittest.main()
    suite = unittest.TestLoader().loadTestsFromTestCase(SSHTestCase)
    unittest.TextTestRunner(verbosity=2).run(suite)