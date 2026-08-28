export type ClaudeTeamsUsageSnapshot = {
  sampledAt: number;
  fetchStatus: string;
  fetchErrorMessage: string | null;
  fiveHourUtilization: number | null;
  fiveHourResetsAt: string | null;
  sevenDayUtilization: number | null;
  sevenDayResetsAt: string | null;
  // True only when the OAuth token itself is expired/revoked
  // ("期限切れ"). Unrelated to fiveHour/sevenDayUtilization, which are
  // separate, self-resetting usage-limit values.
  isTokenExpired: boolean;
};

export type ClaudeTeamsToken = {
  tokenId: string;
  displayName: string;
  enabled: boolean;
  isCoolingDown: boolean;
  createdAt: number;
  lastUsedAt: number | null;
  latestUsage: ClaudeTeamsUsageSnapshot | null;
};

// Only present in the response to createToken (registration time).
// Never returned again -- the admin must copy it out immediately, since
// it isn't shown by the list endpoint.
export type ClaudeTeamsTokenWithIngestSecret = ClaudeTeamsToken & {
  ingestSecret: string;
};

export type CreateClaudeTeamsTokenRequest = {
  displayName: string;
  tokenValue: string;
};

export type UpdateClaudeTeamsTokenRequest = {
  enabled?: boolean;
};
