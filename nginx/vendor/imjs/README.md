# imjs 4.0.0 (`dist/im.min.js`)

The InterMine JavaScript client, served by the mine's nginx at
`/vendor/imjs/im.min.js` and injected into every BlueGenes HTML page as
`window.imjs`, because the BlueGenes npm tools (JBrowse, protein
visualizer, MSA viewer, expression visualizers, Cytoscape network) all
reference that global and BlueGenes no longer provides it
(intermine/bluegenes commit 06363a30, "Remove script loading imjs from
cdn", in images built from `dev` after January 2025).

| | |
|---|---|
| Source | `https://registry.npmjs.org/imjs/-/imjs-4.0.0.tgz`, file `package/dist/im.min.js` |
| Tarball SHA-256 | `18d0e131720bf916855248e2ca62cabe9b467a08c68da30e26458d577457ca09` |
| File SHA-256 | `21f4ebf045bb7cf447a9ea1f82efc57f91208f1ba125afc52f7a198acb3fb8db` |
| Licence | LGPL (see the npm package) |

4.0.0 is the version the tools were written against
(`bluegenes-tool-template` dev dependency `imjs ^4.0.0`) and the last
release; the old CDN URL ended in `latest`. The file is committed rather
than downloaded at build time so the image builds offline and
reproducibly. To upgrade, replace the file and update this table.
