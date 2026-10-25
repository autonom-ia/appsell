import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
TESTS = ROOT / "tests"
DOMAIN_API_ENV = "APPSELL_TEST_DOMAIN_API_URL"


def scripts_that_start_a_domain_api() -> dict[str, str]:
    """Scripts que sobem uma API de verdade e passam o endereço aos testes."""
    return {
        path.relative_to(ROOT).as_posix(): source
        for path in sorted((ROOT / "scripts").glob("*.sh"))
        if f'{DOMAIN_API_ENV}="' in (source := path.read_text(encoding="utf-8"))
    }


class WebDomainApiCoverageTests(unittest.TestCase):
    # #266: três suítes do BFF só rodam com a API de verdade. Sem a variável
    # elas pulam em silêncio no `pnpm test`, e nenhum script com a API as
    # chamava. Pareciam cobertura e não guardavam nada.
    def test_every_web_test_that_needs_the_domain_api_runs_where_it_exists(self) -> None:
        scripts = scripts_that_start_a_domain_api()
        self.assertTrue(scripts)
        orphans = []
        for path in sorted(TESTS.glob("*.test.ts")):
            if DOMAIN_API_ENV not in path.read_text(encoding="utf-8"):
                continue
            relative = path.relative_to(ROOT).as_posix()
            if not any(relative in source for source in scripts.values()):
                orphans.append(relative)

        self.assertEqual([], orphans)


if __name__ == "__main__":
    unittest.main()
