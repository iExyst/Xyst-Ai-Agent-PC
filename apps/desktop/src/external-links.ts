/** Apply the same protocol policy before handing any renderer link to the OS. */
export async function openExternalHttps(url: string, open: (url: string) => Promise<unknown>): Promise<void> {
  const parsed = new URL(url);
  if (parsed.protocol !== "https:") throw new Error("Only HTTPS links can be opened");
  await open(parsed.toString());
}
