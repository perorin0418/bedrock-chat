export type UsageWindow = {
  used: number;
  limit: number;
};

export type UsageStatus = {
  fiveHour: UsageWindow;
  sevenDay: UsageWindow;
};
