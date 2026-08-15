# Sign development builds with a fixed self-signed certificate

Builds are signed with a self-signed certificate held in the developer's login
keychain rather than ad-hoc, because macOS attaches Camera and Accessibility grants
to the app's code signature and an ad-hoc signature changes with every build.

An ad-hoc signature is derived from the bundle's contents. Change a line of Swift and
macOS sees an app it has never met, with no permissions — while System Settings still
shows a `handTrack` row, switched on, granting nothing. Nothing reports this: the
camera light simply never comes on and the cursor never moves. During development this
cost a re-grant per build, which made the app effectively untestable.

Signing with a fixed certificate pins the designated requirement to the certificate
instead of the contents, so it survives a rebuild:

    identifier "com.brohsha.handTrack" and certificate leaf = H"0375d529…"

## Consequences

The certificate needs **both** `keyUsage=digitalSignature` and
`extendedKeyUsage=codeSigning`. With only the latter, macOS reports it as "Invalid Key
Usage for policy" and `codesign` silently falls back to ad-hoc — reintroducing the
original problem while appearing to have fixed it.

It does **not** need to be added to the system trust store. `security find-identity`
reports the certificate as invalid for code signing even where signing with it
succeeds, so `build/make-app.sh` attempts the signature and reports which identity it
actually used rather than trusting that query.

This is a development convenience and nothing more. It is not a distribution
mechanism: the certificate means nothing on any other machine, and shipping to other
people still needs Developer ID signing and notarization.

If Accessibility is switched on but the app still reports itself untrusted, the entry
is stale — clear it with `tccutil reset Accessibility com.brohsha.handTrack` and
relaunch so the app can re-add itself.
