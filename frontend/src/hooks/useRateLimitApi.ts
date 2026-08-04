import useHttp from './useHttp';
import { UsageStatus } from '../@types/rate-limit';

const useRateLimitApi = () => {
  const http = useHttp();

  return {
    getUsageStatus: () => {
      return http.get<UsageStatus>('/user/usage-status', {
        refreshInterval: 60000,
      });
    },
  };
};

export default useRateLimitApi;
