import { startGoogleFontsMock } from "../../scripts/google-fonts-mock.mjs";

// Os testes que sobem `next dev` não vão ao Google Fonts (#457). O mock e o
// motivo estão em scripts/google-fonts-mock.mjs, que o E2E também usa (#513).
export default async function preparar(): Promise<() => Promise<void>> {
  const mock = await startGoogleFontsMock();
  process.env.NEXT_FONT_GOOGLE_MOCKED_RESPONSES = mock.file;
  return async () => {
    await mock.close();
  };
}
