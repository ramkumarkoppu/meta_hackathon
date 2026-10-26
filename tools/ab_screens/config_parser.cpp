// Dummy test file for the Glimmer screen-reading A/B (not real project code).
#include <cctype>
#include <map>
#include <sstream>
#include <stdexcept>
#include <string>
#include <string_view>
#include <utility>

namespace cfg {

struct Entry {
    std::string key;
    std::string value;
    int line = 0;
};

static std::string_view trim(std::string_view s) {
    while (!s.empty() && std::isspace(static_cast<unsigned char>(s.front()))) s.remove_prefix(1);
    while (!s.empty() && std::isspace(static_cast<unsigned char>(s.back()))) s.remove_suffix(1);
    return s;
}

// Parses "key = value" lines; '#' starts a comment, "[section]" prefixes keys.
std::map<std::string, Entry> parse(const std::string& text) {
    std::map<std::string, Entry> out;
    std::istringstream in(text);
    std::string raw, section;
    int line_no = 0;
    while (std::getline(in, raw)) {
        ++line_no;
        auto line = trim(std::string_view(raw).substr(0, raw.find('#')));
        if (line.empty()) continue;
        if (line.front() == '[' && line.back() == ']') {
            section = std::string(line.substr(1, line.size() - 2)) + ".";
            continue;
        }
        const auto eq = line.find('=');
        if (eq == std::string_view::npos) {
            throw std::runtime_error("line " + std::to_string(line_no) + ": expected 'key = value'");
        }
        auto [it, inserted] = out.try_emplace(section + std::string(trim(line.substr(0, eq))));
        if (!inserted) {
            throw std::runtime_error("duplicate key '" + it->first + "'");
        }
        it->second = Entry{it->first, std::string(trim(line.substr(eq + 1))), line_no};
    }
    return out;
}

constexpr double kEpsilon = 1e-9;

bool as_bool(const Entry& e) {
    switch (e.value.empty() ? '\0' : std::tolower(static_cast<unsigned char>(e.value[0]))) {
        case 't': case 'y': case '1': return true;
        case 'f': case 'n': case '0': return false;
        default: throw std::invalid_argument(e.key + ": not a boolean (\"" + e.value + "\")");
    }
}

}  // namespace cfg
