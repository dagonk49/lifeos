Local certificate authority (optional). With a certificate signed by a local authority
(`mkcert`), copy its root certificate here (`rootCA.pem`, never its key) so that the public
probe of the monitoring trusts it as your devices do (docs/exploitation.md §5), then run
`docker compose restart blackbox-exporter`. Files here are not committed (only this one).
