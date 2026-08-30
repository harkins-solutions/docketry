"""Knowing who an address belongs to, and keeping the two axes apart."""
import tempfile
import unittest
from pathlib import Path

from docketry.tools.contacts import (
    ContactError,
    load_contacts,
    load_if_present,
)
from docketry.core.roles import load_roles

EXAMPLE = Path("examples/contacts.toml")
ROLES = Path("examples/roles.toml")


def _file(body: str) -> Path:
    p = Path(tempfile.mkdtemp()) / "contacts.toml"
    p.write_text(body)
    return p


class TestLoad(unittest.TestCase):
    def test_the_shipped_example_loads_against_the_shipped_roles(self):
        d = load_contacts(EXAMPLE, load_roles(ROLES))
        self.assertGreater(len(d), 0)

    def test_the_shipped_example_names_no_real_person_or_firm(self):
        text = EXAMPLE.read_text()
        # Every address in the file must be an obvious placeholder.
        for line in text.splitlines():
            if line.strip().startswith("email"):
                self.assertIn(".example", line, f"real-looking address: {line}")

    def test_an_address_without_an_at_sign_is_refused(self):
        with self.assertRaises(ContactError) as ctx:
            load_contacts(_file('[[contact]]\nemail = "notanaddress"\n'))
        self.assertIn("neither an address nor a domain", str(ctx.exception))

    def test_an_unknown_kind_lists_the_real_ones(self):
        with self.assertRaises(ContactError) as ctx:
            load_contacts(_file('[[contact]]\nemail="a@b.com"\nkind="enemy"\n'))
        self.assertIn("opposing_counsel", str(ctx.exception))

    def test_absent_file_is_not_an_error(self):
        self.assertIsNone(load_if_present(tempfile.mkdtemp()))


class TestTheTwoAxesStayApart(unittest.TestCase):
    def test_only_staff_may_hold_a_role(self):
        # Otherwise "opposing counsel" becomes a thing that can clear a hold.
        with self.assertRaises(ContactError) as ctx:
            load_contacts(_file('[[contact]]\nemail="oc@them.com"\n'
                                'kind="opposing_counsel"\nroles=["attorney"]\n'))
        self.assertIn("only a staff contact holds roles", str(ctx.exception))

    def test_a_staff_role_must_be_one_the_firm_declared(self):
        from docketry.core.roles import RoleError
        with self.assertRaises(RoleError):
            load_contacts(_file('[[contact]]\nemail="a@b.com"\nkind="staff"\n'
                                'roles=["wizard"]\n'), load_roles(ROLES))


class TestLookup(unittest.TestCase):
    def setUp(self):
        self.d = load_contacts(_file(
            '[[contact]]\nemail="kelly@ourfirm.com"\nname="Kelly"\n'
            'kind="staff"\nroles=["paralegal"]\n'
            '[[contact]]\nemail="@theirfirm.com"\nkind="opposing_counsel"\n'
            '[[contact]]\nemail="mr.doe@client.com"\nkind="client"\n'))

    def test_an_exact_address_wins(self):
        self.assertEqual(self.d.kind_of("kelly@ourfirm.com"), "staff")

    def test_a_domain_entry_covers_everyone_there(self):
        self.assertEqual(self.d.kind_of("anyone@theirfirm.com"),
                         "opposing_counsel")

    def test_lookup_is_case_insensitive(self):
        self.assertEqual(self.d.kind_of("KELLY@OurFirm.com"), "staff")

    def test_an_unknown_address_is_other_not_a_guess(self):
        self.assertEqual(self.d.kind_of("stranger@nowhere.com"), "other")

    def test_client_mail_is_flagged_privileged(self):
        self.assertTrue(self.d.is_privileged("mr.doe@client.com"))
        self.assertFalse(self.d.is_privileged("anyone@theirfirm.com"))

    def test_a_listed_person_is_held_to_their_roles(self):
        self.assertTrue(self.d.may_claim("kelly@ourfirm.com", "paralegal"))
        self.assertFalse(self.d.may_claim("kelly@ourfirm.com", "attorney"))

    def test_an_unlisted_person_is_not_refused(self):
        self.assertTrue(self.d.may_claim("new@ourfirm.com", "paralegal"))


if __name__ == "__main__":
    unittest.main()
