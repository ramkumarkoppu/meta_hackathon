// Dummy test file for the Glimmer screen-reading A/B (not real project code). Lines are long on purpose.
#include <algorithm>
#include <functional>
#include <string>
#include <vector>

struct Sample { std::string name; double value; int priority; };

std::vector<Sample> select_top(const std::vector<Sample>& samples, std::size_t limit, const std::function<bool(const Sample&)>& keep) {
    std::vector<Sample> picked;
    std::copy_if(samples.begin(), samples.end(), std::back_inserter(picked), [&keep](const Sample& s) { return keep(s) && s.value >= 0.0; });
    std::stable_sort(picked.begin(), picked.end(), [](const Sample& a, const Sample& b) { return a.priority != b.priority ? a.priority > b.priority : a.value > b.value; });
    if (picked.size() > limit) picked.erase(picked.begin() + static_cast<std::ptrdiff_t>(limit), picked.end());
    return picked;
}

std::string describe(const std::vector<Sample>& picked) {
    std::string out = "selected " + std::to_string(picked.size()) + " samples: ";
    for (std::size_t i = 0; i < picked.size(); ++i) out += (i ? ", " : "") + picked[i].name + "=" + std::to_string(picked[i].value) + " (p" + std::to_string(picked[i].priority) + ")";
    return out.empty() ? std::string("none") : out;
}
