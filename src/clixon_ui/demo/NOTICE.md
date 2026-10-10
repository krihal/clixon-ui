# Third-party material in the demo

Bundled files:

- `yang/clixon-*.yang`: Clixon, Apache-2.0.
- `yang/ietf-*.yang`: IETF Trust. The copyright headers inside the files must stay.
- `yang/demo-*.yang` and `device_yang/*.yang`: written for this demo.

Not bundled: device configuration and RPC replies in `seed.py` are invented. They only follow the structure of the
public OpenConfig models and of Juniper's Junos models. No OpenConfig, Arista or Juniper model files are included.
If you add any third-party file, check its licence (and its per-file header) and list it here.

## Licences checked (2026-10)

| Source | Licence | Link |
|---|---|---|
| Clixon controller | Apache-2.0 | https://github.com/clicon/clixon-controller/blob/main/LICENSE |
| OpenConfig models | Apache-2.0 | https://github.com/openconfig/public/blob/master/LICENSE |
| Arista `aristanetworks/yang` | Apache-2.0 | https://github.com/aristanetworks/yang/blob/master/LICENSE |
| Juniper `Juniper/yang` | Apache-2.0 at the repo root, but the YANG files say "Copyright Juniper Networks, Inc. All rights reserved" | https://github.com/Juniper/yang/blob/master/LICENSE |
| IETF modules | IETF Trust Legal Provisions (BSD-style) | https://trustee.ietf.org/license-info/IETF-TLP-5.htm |
| Apache-2.0 text | | https://www.apache.org/licenses/LICENSE-2.0 |

Looked at and not used:

| Source | Licence | Link |
|---|---|---|
| Cisco XE / NX models | Cisco API License 1.1 (not open source) | https://github.com/YangModels/yang/blob/main/vendor/cisco/xe/LICENSE.md |
| Cisco XR models | no licence grant in the files | |
| Nokia SR Linux models | BSD-3-Clause | https://github.com/nokia/srlinux-yang-models/blob/master/LICENSE |
| SONiC yang models | Apache-2.0 (dropped: no Clixon support) | https://github.com/sonic-net/sonic-buildimage/blob/master/src/sonic-yang-models/LICENSE |
