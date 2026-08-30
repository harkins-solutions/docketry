# Security

## If it involves real client data, do not open an issue

The issue tracker is public and permanent. If reporting something would mean
posting a client's name, a case number, a document or an address, stop and
email **Joshua@HarkinsSolutionsSystemsGroup.com** instead.

You do not need a GitHub account to do that, and you should not have to make
one to tell us something went wrong with a client's file. If you would rather
use GitHub, [private vulnerability reporting][pvr] opens a report only the
maintainers can read.

[pvr]: https://github.com/harkins-solutions/docketry/security/advisories/new

If you do need to send us a document, redact it with Docketry first — it
removes the words rather than covering them — and read what it reports. A box
that comes back **unverifiable** covered no readable text, so the content is
gone but nothing could be confirmed; look at that page yourself before it
leaves your machine.

That guidance stops where the tool is the problem. If Docketry is what
mishandled something — a redaction that did not hold, data written somewhere
it should not have been — do not redact with it and send it to us. Tell us what
happened and we will work out how to reproduce it without the original.

## Reporting a vulnerability

Email **Joshua@HarkinsSolutionsSystemsGroup.com**, or use [private vulnerability reporting][pvr] if you prefer
to keep it on GitHub. Please include what you did, what happened, and the
version. We will confirm receipt, tell you what we find, and
credit you in the release notes unless you would rather we did not.

Do not open a public issue for a vulnerability, and please do not post a
working exploit anywhere while it is unfixed.

## What Docketry is and is not

Worth knowing before you decide something is a vulnerability, because two of
these are deliberate and documented:

- **It is not a security product.** It makes no malware, phishing or antivirus
  claims. Its sender and attachment gates are pipeline-hygiene policy — what
  the pipeline will accept — and nothing more.
- **It does not encrypt at rest.** Messages and attachments sit in plaintext
  SQLite and files, protected by file permissions. An encrypted database whose
  key is on the same disk is comfort, not protection; full-disk encryption is
  the control, and the README says so. `config.toml` is created 0600 on POSIX;
  on Windows file permissions are inherited from the folder, so a password
  belongs in `DOCKETRY_IMAP_PASSWORD` rather than in the file.
- **It has no login.** The review UI binds to 127.0.0.1 only and refuses any
  other interface. Roles say what a hold is waiting for and are recorded
  against a name; they are an attestation, not authentication. A report that
  "anyone can claim any role" is describing the design, though a report that
  the UI can be reached from another machine is a real finding and we want it.
- **It is local.** Docketry does not send your documents anywhere. A configured
  model must resolve to your own network or the request is refused before it is
  built. If you find a path that sends data off the machine without the
  operator configuring it, that is a serious bug — please report it privately.

## Supported versions

The latest release. Docketry is early; fixes go forward rather than into
patches of older tags.
