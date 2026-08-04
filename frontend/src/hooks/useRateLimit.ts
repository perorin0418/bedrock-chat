import useRateLimitApi from './useRateLimitApi';

const useRateLimit = () => {
  const { getUsageStatus } = useRateLimitApi();
  const { data, isLoading, error } = getUsageStatus();

  return {
    usageStatus: data,
    isLoading,
    error,
  };
};

export default useRateLimit;
