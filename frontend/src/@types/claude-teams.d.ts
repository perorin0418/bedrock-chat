export type ClaudeTeamsToken = {
  tokenId: string;
  displayName: string;
  enabled: boolean;
  isCoolingDown: boolean;
  createdAt: number;
  lastUsedAt: number | null;
};

export type CreateClaudeTeamsTokenRequest = {
  displayName: string;
  tokenValue: string;
};

export type UpdateClaudeTeamsTokenRequest = {
  enabled?: boolean;
};
