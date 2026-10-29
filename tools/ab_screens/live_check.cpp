// live check: a dummy file to type into a new VS Code tab
#include <vector>

int sum_first(const std::vector<int>& v, int n) {
    int total = 0;
    for (int i = 0; i <= n; ++i) {
        total += v[i];
    }
    return total;
}
