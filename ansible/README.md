# EdSys Ansible

This directory contains narrow, source-controlled acceptance and operations
playbooks. It does not contain private keys, live inventory addresses, passwords,
tokens, or runtime exports.

## EdCore acceptance

The EdCore inventory resolves through the private `edcore-admin` SSH alias on the
canonical 9950x host:

```bash
ansible-playbook \
  -i ansible/inventory/edcore-workhorse.yml \
  ansible/playbooks/edcore-control-acceptance.yml
```

Use `EDCORE_SSH_HOST=edcore-admin-tailnet` with the control helper for Tailnet
fallback checks. The private Tailnet address remains in the operator's SSH config,
not in this repository.
