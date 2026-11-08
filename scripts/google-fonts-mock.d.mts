export declare const GOOGLE_FONTS: ReadonlyArray<{
  readonly url: string;
  readonly family: string;
  readonly faces: ReadonlyArray<{ readonly weight: string; readonly file: string }>;
}>;

export declare function startGoogleFontsMock(): Promise<{
  readonly file: string;
  close(): Promise<void>;
}>;
