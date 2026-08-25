import {
  ClaudeTeamsToken,
  CreateClaudeTeamsTokenRequest,
  UpdateClaudeTeamsTokenRequest,
} from '../@types/claude-teams';
import useHttp from './useHttp';

const useClaudeTeamsTokens = () => {
  const http = useHttp();
  const { data, isLoading, mutate } = http.get<ClaudeTeamsToken[]>(
    '/admin/claude-teams-tokens'
  );

  const createToken = async (req: CreateClaudeTeamsTokenRequest) => {
    await http.post<ClaudeTeamsToken>('/admin/claude-teams-tokens', req);
    await mutate();
  };

  const updateToken = async (
    tokenId: string,
    req: UpdateClaudeTeamsTokenRequest
  ) => {
    await http.patch<null>(`/admin/claude-teams-tokens/${tokenId}`, req);
    await mutate();
  };

  const deleteToken = async (tokenId: string) => {
    await http.delete<null>(`/admin/claude-teams-tokens/${tokenId}`);
    await mutate();
  };

  return {
    tokens: data,
    isLoading,
    createToken,
    updateToken,
    deleteToken,
  };
};

export default useClaudeTeamsTokens;
