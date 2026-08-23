text = File.read("Gemfile")
unless text.include?('gem "repomin-required"')
  puts "DIFFERENT_FAILURE"
  exit 2
end
puts "ORIGINAL_FAILURE"
exit 1
