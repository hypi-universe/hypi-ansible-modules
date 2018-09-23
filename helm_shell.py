#!/usr/bin/python

ANSIBLE_METADATA = {
    'metadata_version': '1.1',
    'status': ['preview'],
    'supported_by': 'community'
}

DOCUMENTATION = '''
Depends on `helm` command being available on the host it is executed on.
To run on the host where the playbook was executed, use delegate_to e.g.
```
  roles:
    - role: hypi-charts
      delegate_to: 127.0.0.1
```
It assumes kubectl is configured to point to the correct environment so before running, set this using e.g.
```
kubectl config use-context staging
```
'''

EXAMPLES = '''
- name: Install Rook Ceph Operator
  helm_shell:
    namespace: default
    name: rook-ceph
    version: 0.8.0
    source:
      type: directory
      location: "{{ role_path }}/files/platform/rook" # could also use some lookup mechanism "{{lookup('<some-lookup-plugin>', '<path-to-chart>')}}"
'''

RETURN = '''
'''

import os.path

from ansible.module_utils.basic import AnsibleModule
from pkg_resources import parse_version


def run_module():
    # TODO add support for global helm args --debug, --home, --host, --kube-context, --tiller-namespace
    # TODO add support for install --verify, --replace and expand check_mode support to use helm's --dry-run
    # TODO add support for upgrade --verify, --force --recreate-pods and expand check_mode support to use helm's --dry-run
    module_args = dict(
        name=dict(type='str', required=True),
        version=dict(type='str', required=False),
        source=dict(type='dict', required=True),
        namespace=dict(type='str', required=False, default='default'),
        state=dict(type='str', required=False, default='present')
    )

    # seed the result dict in the object
    # we primarily care about changed and state
    # change is if this module effectively modified the target
    # state will include any data that you want your module to pass back
    # for consumption, for example, in a subsequent task
    result = dict(
        changed=False,
        original_message='',
        message=''
    )

    # the AnsibleModule object will be our abstraction working with Ansible
    # this includes instantiation, a couple of common attr would be the
    # args/params passed to the execution, as well as if the module
    # supports check mode
    module = AnsibleModule(
        argument_spec=module_args,
        supports_check_mode=True
    )

    # if the user is working with this module in only check mode we do not
    # want to make any changes to the environment, just return the current
    # state with no modifications
    if module.check_mode:
        return result

    chart_namespace = module.params['namespace']
    chart_state = module.params['state']
    chart_name = module.params['name']
    chart_version = parse_version(module.params['version'])
    chart_location = module.params['source']['location']
    # (rc, out, err) = module.run_command("pwd && ls", use_unsafe_shell=True)
    # return module.fail_json(msg=module.jsonify([rc, out, err]))
    if chart_state == 'absent':
        cmd_str = "helm delete '%s'" % chart_name
        (rc, out, err) = module.run_command(cmd_str, use_unsafe_shell=True)
        if rc:
            return module.fail_json(msg=err, rc=rc, cmd=cmd_str)
        result['changed'] = True
        result['message'] = 'Deleted chart %s' % chart_name
        result['original_message'] = out
        return module.exit_json(**result)

    req_file = os.path.join(chart_location, 'requirements.yaml')
    if os.path.isfile(req_file):
        cmd_str = "helm dependency update && helm build %s" % chart_location
        (rc, out, err) = module.run_command(cmd_str, use_unsafe_shell=True)
        if rc:
            return module.fail_json(msg=err, rc=rc, cmd=cmd_str)

    cmd_str = "helm ls --all | grep '%s' | cut -f 4,5 | xargs" % chart_name
    (rc, out, err) = module.run_command(cmd_str, use_unsafe_shell=True)
    if rc:
        return module.fail_json(msg=err, rc=rc, cmd=cmd_str)
    # multiple lines can be returned if the chart is installed under different names and grep matches a substring
    # so we take the latest one i.e. very last one of the list
    out = out.splitlines()[-1:]

    if len(out) == 0 or not out[-1].strip():  # chart doesn't exist first time, install
        cmd_str = "helm install --namespace='%s' --name='%s' %s --version %s" % (chart_namespace, chart_name, chart_location, chart_version)
        (rc, out, err) = module.run_command(cmd_str, use_unsafe_shell=True)
        if rc:
            return module.fail_json(msg=err, rc=rc, cmd=cmd_str)
        result['changed'] = True
        result['message'] = 'Installed chart %s, version %s' % (chart_name, chart_version)
        result['original_message'] = out
        return module.exit_json(**result)
    elif out[-1].split()[0].lower() == 'deleted':
        cmd_str = "helm install --namespace='%s' --name='%s' --replace %s" % (chart_namespace, chart_name, chart_location)
        (rc, out, err) = module.run_command(cmd_str, use_unsafe_shell=True)
        if rc:
            return module.fail_json(msg=err, rc=rc, cmd=cmd_str)
        result['changed'] = True
        result['message'] = 'Re-installed (previously deleted) chart %s, version %s' % (chart_name, chart_version)
        result['original_message'] = out
        return module.exit_json(**result)
    else:
        out = out[-1].split()  # we split lines and get an array back, now know it's non-empty so take last item
        deployment_status = out[0]
        deployed_version = parse_version(out[1].split('-')[-1])
        if deployment_status.lower() != "deployed" or chart_version > deployed_version:
            module.debug("Upgrading %s, deployed: %s, deploying: %s, current status: %s" % (
                chart_name, deployed_version, chart_version, deployment_status))
            cmd_str = "helm upgrade %s %s" % (chart_name, chart_location)
            (rc, out, err) = module.run_command(cmd_str, use_unsafe_shell=True)
            if rc:
                return module.fail_json(msg=err, rc=rc, cmd=cmd_str)
            result['changed'] = True
            result['message'] = 'Upgraded chart %s, version %s' % (chart_name, chart_version)
            result['original_message'] = out
            return module.exit_json(**result)
        elif chart_version < deployed_version:
            cmd_str = "helm history %s | grep '%s' | cut -f 1" % (chart_name, chart_version)
            (rc, out, err) = module.run_command(cmd_str, use_unsafe_shell=True)
            if rc:
                return module.fail_json(msg=err, rc=rc, cmd=cmd_str)
            if not out.strip():
                return module.fail_json(msg="Cannot downgrade %s has never been deployed at version %s. "
                                            "Its status is currently %s at a version of '%s'" % (
                                                chart_name, chart_version, deployment_status.lower(),
                                                str(deployed_version)))
            # Multiple revisions can have the same version
            # each one will be on their own line so take the newest i.e. the lat one
            deployed_revision = out.splitlines()[-1]
            cmd_str = "helm rollback %s %s" % (chart_name, deployed_revision)
            # return module.fail_json(msg=[out,deployed_revision,cmd_str])
            (rc, out, err) = module.run_command(cmd_str, use_unsafe_shell=True)
            if rc:
                return module.fail_json(msg=err, rc=rc, cmd=cmd_str)
            result['changed'] = True
            result['message'] = 'Rolled back chart %s to version version %s, revision %s' % (
                chart_name, chart_version, deployed_revision)
            result['original_message'] = out
            return module.exit_json(**result)
    module.exit_json(**result)


def main():
    run_module()


if __name__ == '__main__':
    main()
