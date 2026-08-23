# Shared bootstrap SSH key

`comma_setup.b64` is the intentionally public vamOS bootstrap key. It is
encoded as a repository asset because GitHub push protection blocks PEM
private-key envelopes even when they are deliberately shared. Fresh devices
authorize its public key once on first boot so that anyone with the bootstrap
private key can perform initial setup over any reachable network path. Replace
or remove it through `/data/params/d/GithubSshKeys` after provisioning.

Host tools materialize the checked-in key with mode 0600 before passing it to
OpenSSH.
