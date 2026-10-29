export function disconnectAgentParent(channel: Pick<NodeJS.Process, "connected" | "disconnect">): void {
  if (channel.connected) channel.disconnect();
}
