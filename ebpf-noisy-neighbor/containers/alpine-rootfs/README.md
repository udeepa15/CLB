# Alpine rootfs workspace

`setup-rootfs.sh` will create and manage:

- `rootfs-template/` (base Alpine + packages)
- runC bundles in `../runtime/{tenant1,tenant2,noisy}/`

You normally do not edit files here manually.
