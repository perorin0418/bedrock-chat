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

  const downloadUsageHistoryCsv = async (params: {
    start: number;
    end: number;
    tokenId?: string;
  }) => {
    const response = await http.getBlob(
      '/admin/claude-teams-tokens/usage-history/csv',
      params
    );
    const url = window.URL.createObjectURL(new Blob([response.data]));
    const link = document.createElement('a');
    link.href = url;
    link.download = `claude-teams-usage-history-${params.start}-${params.end}.csv`;
    document.body.appendChild(link);
    link.click();
    link.remove();
    window.URL.revokeObjectURL(url);
  };

  return {
    tokens: data,
    isLoading,
    createToken,
    updateToken,
    deleteToken,
    downloadUsageHistoryCsv,
  };
};

export default useClaudeTeamsTokens;
