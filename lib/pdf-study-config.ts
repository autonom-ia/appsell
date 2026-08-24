export function pdfStudyEnabled(
  environment: Partial<Record<string, string | undefined>> = process.env
) {
  const configured = environment.APPSELL_PDF_STUDY_ENABLED?.trim().toLowerCase();
  return configured === undefined || configured === "true";
}
