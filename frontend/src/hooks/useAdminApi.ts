import {
  ListBotApisRequest,
  ListBotApisResponse,
  ListPublicBotsRequest,
  ListPublicBotsResponse,
} from '../@types/api-publication';
import { GetPublicBotResponse, UpdateBotPushedRequest } from '../@types/bot';
import { ListPendingUsersResponse } from '../@types/user';
import useHttp from './useHttp';

const useAdminApi = () => {
  const http = useHttp();

  return {
    listBotApis: (req: ListBotApisRequest) => {
      return http.get<ListBotApisResponse>(['/admin/published-bots', req]);
    },
    listPublicBots: (req: ListPublicBotsRequest) => {
      return http.get<ListPublicBotsResponse>(
        !!req.start === !!req.end ? ['/admin/public-bots', req] : null
      );
    },
    getPublicBot: (botId: string) => {
      return http.get<GetPublicBotResponse>(`/admin/bot/public/${botId}`);
    },
    updatePinnedBot: (botId: string, params: UpdateBotPushedRequest) => {
      return http.patch<null>(`/admin/bot/${botId}/pushed`, params);
    },
    listPendingUsers: () => {
      return http.get<ListPendingUsersResponse>('/admin/users/pending');
    },
    approveUser: (userId: string) => {
      return http.patch<null>(`/admin/users/${userId}/approve`, {});
    },
  };
};

export default useAdminApi;
